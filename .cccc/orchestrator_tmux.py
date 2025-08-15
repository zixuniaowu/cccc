# -*- coding: utf-8 -*-
"""
CCCC Orchestrator (tmux + long-lived CLI sessions)
- 左右两 pane 分别运行 PeerA(Claude) / PeerB(Codex) 的交互式会话（长连接）
- 通过 tmux 粘贴消息/抓取输出，解析 <TO_USER>/<TO_PEER> 与 ```patch```，执行预检/应用/测试/记账
- 自动将“角色核心+人格+策略”织成 [SYSTEM] 注入，并支持热更新
"""
import os, re, sys, json, time, shlex, tempfile, fnmatch, subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

ANSI_RE = re.compile(r"\x1b\[.*?m|\x1b\[?[\d;]*[A-Za-z]")  # 去色
PATCH_RE = re.compile(r"```(?:patch|diff)\s*([\s\S]*?)```", re.I)
SECTION_RE_TPL = r"<{tag}>([\s\S]*?)</{tag}>"

def run(cmd: str, *, cwd: Optional[Path]=None, timeout: int=600) -> Tuple[int,str,str]:
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(cwd) if cwd else None)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill(); return 124, "", "Timeout"
    return p.returncode, out, err

def ensure_bin(name: str):
    code,_,_ = run(f"command -v {shlex.quote(name)}")
    if code != 0:
        print(f"[FATAL] 需要可执行: {name}")
        raise SystemExit(1)

def ensure_git_repo():
    code, out, _ = run("git rev-parse --is-inside-work-tree")
    if code != 0 or "true" not in out:
        print("[FATAL] 当前目录不是 git 仓库。请先：git init && git add -A && git commit -m 'init'")
        raise SystemExit(1)

def strip_ansi(s: str) -> str: return ANSI_RE.sub("", s)
def parse_section(text: str, tag: str) -> str:
    m = re.search(SECTION_RE_TPL.format(tag=tag), text, re.I)
    return (m.group(1).strip() if m else "")

def extract_patches(text: str) -> List[str]:
    out=[]
    for m in PATCH_RE.finditer(text):
        body=m.group(1).strip()
        if not body.startswith("*** PATCH"):
            body=f"*** PATCH\n{body}\n*** END PATCH"
        out.append(body)
    return out

def count_changed_lines(patch: str) -> int:
    n=0
    for ln in patch.splitlines():
        if ln.startswith("+++ ") or ln.startswith("--- "): continue
        if ln.startswith("+") or ln.startswith("-"): n+=1
    return n

def extract_paths_from_patch(patch: str) -> List[str]:
    paths=set()
    for ln in patch.splitlines():
        if ln.startswith("--- ") or ln.startswith("+++ "):
            pth=ln.split("\t")[0].split(" ",1)[1].strip()
            if pth.startswith("a/") or pth.startswith("b/"): pth=pth[2:]
            paths.add(pth)
    return sorted(paths)

# ---------- tmux ----------
def tmux(*args: str) -> Tuple[int,str,str]:
    return run("tmux " + " ".join(shlex.quote(a) for a in args))

def tmux_session_exists(name: str) -> bool:
    code,_,_ = tmux("has-session","-t",name); return code==0

def tmux_new_session(name: str) -> Tuple[str,str]:
    code,out,err = tmux("new-session","-d","-s",name,"-P","-F","#S:#I.#P")
    if code!=0: raise RuntimeError(f"tmux new-session 失败: {err}")
    tmux("split-window","-h","-t",name)
    code3,out3,_ = tmux("list-panes","-t",name,"-F","#P")
    panes = out3.strip().splitlines()
    return panes[0], panes[1]

def tmux_paste(pane: str, text: str):
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
        f.write(text); fname=f.name
    buf = f"buf-{int(time.time()*1000)}"
    tmux("load-buffer","-b",buf,fname); tmux("paste-buffer","-t",pane,"-b",buf); tmux("send-keys","-t",pane,"Enter"); tmux("delete-buffer","-b",buf)
    try: os.unlink(fname)
    except Exception: pass

def tmux_capture(pane: str, lines: int=2000) -> str:
    code,out,err = tmux("capture-pane","-t",pane,"-p","-S",f"-{lines}")
    return strip_ansi(out if code==0 else "")

def tmux_start_interactive(pane: str, cmd: str):
    tmux_paste(pane, cmd)

# ---------- YAML & prompts ----------
def read_yaml(p: Path) -> Dict[str,Any]:
    if not p.exists(): return {}
    try:
        import yaml; return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except ImportError:
        d={}; 
        for line in p.read_text(encoding="utf-8").splitlines():
            line=line.strip()
            if not line or line.startswith("#") or ":" not in line: continue
            k,v=line.split(":",1); d[k.strip()]=v.strip().strip('"\'')
        return d

def read_text(p: Path) -> str:
    if not p.exists():
        print(f"[FATAL] 缺少文件: {p}"); raise SystemExit(1)
    return p.read_text(encoding="utf-8")

# ---------- ledger & policies ----------
def log_ledger(home: Path, entry: Dict[str,Any]):
    state = home/"state"; state.mkdir(exist_ok=True)
    entry={"ts":time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
    with (state/"ledger.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps(entry,ensure_ascii=False)+"\n")

def allowed_by_policies(paths: List[str], policies: Dict[str,Any]) -> bool:
    allowed = policies.get("patch_queue",{}).get("allowed_paths",["**"])
    for pth in paths:
        if not any(fnmatch.fnmatch(pth,pat) for pat in allowed):
            print(f"[POLICY] 路径不允许: {pth}")
            return False
    return True

def try_lint(): 
    LINT_CMD=os.environ.get("LINT_CMD","").strip()
    if not LINT_CMD: return
    code,out,err=run(LINT_CMD); 
    print("[LINT]", "通过" if code==0 else "失败"); 
    if out.strip(): print(out.strip()); 
    if err.strip(): print(err.strip())

def try_tests() -> bool:
    TEST_CMD=os.environ.get("TEST_CMD","").strip()
    if not TEST_CMD:
        print("[TEST] 跳过（未设 TEST_CMD）"); return True
    code,out,err=run(TEST_CMD)
    ok=(code==0)
    print("[TEST]", "通过" if ok else "失败")
    if out.strip(): print(out.strip())
    if err.strip(): print(err.strip())
    return ok

def git_apply_check(patch: str) -> Tuple[bool,str]:
    with tempfile.NamedTemporaryFile("w",delete=False,suffix=".patch") as f:
        f.write(patch); path=f.name
    code,out,err=run(f"git apply --check {shlex.quote(path)}")
    return (code==0, (out+err))

def git_apply(patch: str) -> Tuple[bool,str]:
    with tempfile.NamedTemporaryFile("w",delete=False,suffix=".patch") as f:
        f.write(patch); path=f.name
    code,out,err=run(f"git apply {shlex.quote(path)}")
    return (code==0, (out+err))

def git_commit(msg: str):
    run("git add -A"); run(f"git commit -m {shlex.quote(msg)}")

# ---------- prompt weaving ----------
def weave_system(home: Path, peer: str) -> str:
    from prompt_weaver import weave_system_prompt
    return weave_system_prompt(home, peer)

def list_repo_files(limit:int=200)->str:
    code,out,_ = run("git ls-files")
    return "\n".join(out.splitlines()[:limit])

def context_blob(policies: Dict[str,Any], phase: str) -> str:
    return (f"# PHASE: {phase}\n# REPO FILES (partial):\n{list_repo_files()}\n\n"
            f"# POLICIES:\n{json.dumps({'patch_queue':policies.get('patch_queue',{}),'rfd':policies.get('rfd',{}),'autonomy_level':policies.get('autonomy_level')},ensure_ascii=False)}\n")

# ---------- watcher ----------
def snapshot_mtime(paths: List[Path]) -> Dict[str,float]:
    out={}
    for p in paths: out[str(p)]= (p.stat().st_mtime if p.exists() else 0.0)
    return out

def changed_files(prev: Dict[str,float], paths: List[Path]) -> bool:
    for p in paths:
        cur = (p.stat().st_mtime if p.exists() else 0.0)
        if cur != prev.get(str(p), -1): return True
    return False

# ---------- EXCHANGE ----------
def print_block(title: str, body: str):
    if not body.strip(): return
    print(f"\n======== {title} ========\n{body.strip()}\n")

def exchange_once(home: Path, sender_pane: str, receiver_pane: str, payload: str,
                  context: str, who: str, policies: Dict[str,Any], phase: str):
    tmux_paste(sender_pane, f"[CONTEXT]\n{context}\n\n[INPUT]\n{payload}\n")
    time.sleep(2.5)  # 粗略等待输出稳定
    content = tmux_capture(sender_pane, lines=2000)
    # 解析最新的三分区
    def last(tag): 
        items=re.findall(SECTION_RE_TPL.format(tag=tag), content, re.I)
        return (items[-1].strip() if items else "")
    to_user = last("TO_USER"); to_peer = last("TO_PEER")
    print_block(f"{who} → USER", to_user); 
    log_ledger(home, {"from":who,"kind":"to_user","chars":len(to_user)})

    patches = extract_patches(content)
    for i,patch in enumerate(patches,1):
        print_block(f"{who} 补丁#{i}", "预检中 …")
        lines = count_changed_lines(patch)
        max_lines = int(policies.get("patch_queue",{}).get("max_diff_lines",150))
        if lines>max_lines:
            print(f"[POLICY] 改动行数 {lines} > {max_lines}，拒绝。")
            log_ledger(home, {"from":who,"kind":"patch-reject","reason":"too-many-lines","lines":lines}); 
            continue
        paths = extract_paths_from_patch(patch)
        if not allowed_by_policies(paths, policies):
            log_ledger(home, {"from":who,"kind":"patch-reject","reason":"path-not-allowed","paths":paths}); 
            continue
        ok,err = git_apply_check(patch)
        if not ok:
            print("[PATCH] 预检失败：\n"+err.strip()); 
            log_ledger(home, {"from":who,"kind":"patch-precheck-fail","stderr":err.strip()[:2000]}); 
            continue
        ok2,err2 = git_apply(patch)
        if not ok2:
            print("[PATCH] 应用失败：\n"+err2.strip()); 
            log_ledger(home, {"from":who,"kind":"patch-apply-fail","stderr":err2.strip()[:2000]}); 
            continue
        try_lint()
        tests_ok = try_tests()
        git_commit(f"cccc({who}): apply patch (phase {phase})")
        log_ledger(home, {"from":who,"kind":"patch-commit","paths":paths,"lines":lines,"tests_ok":tests_ok})
        if not tests_ok:
            fb = "<TO_PEER>\ntype: EVIDENCE\nintent: fix\ntasks:\n  - desc: '测试失败，请提供最小修复补丁'\n</TO_PEER>\n"
            tmux_paste(sender_pane, f"[INPUT]\n{fb}\n")

    if to_peer.strip():
        tmux_paste(receiver_pane, f"[INPUT]\n{to_peer}\n")
        log_ledger(home, {"from":who,"kind":"handoff","chars":len(to_peer)})

# ---------- MAIN ----------
def main(home: Path):
    ensure_bin("tmux"); ensure_git_repo()
    # 目录
    settings = home/"settings"; prompts = home/"prompts"; personas = home/"personas"; state = home/"state"
    state.mkdir(exist_ok=True)

    roles    = read_yaml(settings/"roles.yaml")
    policies = read_yaml(settings/"policies.yaml")

    leader   = (roles.get("leader") or "peerA").strip().lower()
    session  = f"cccc-{Path.cwd().name}"

    # 准备 tmux 会话/面板
    if not tmux_session_exists(session):
        left,right = tmux_new_session(session)
        (state/"session.json").write_text(json.dumps({"session":session,"left":left,"right":right}), encoding="utf-8")
    else:
        code,out,_ = tmux("list-panes","-t",session,"-F","#P")
        panes=out.strip().splitlines()
        left,right = panes[0], panes[1] if len(panes)>1 else panes[0]
        (state/"session.json").write_text(json.dumps({"session":session,"left":left,"right":right}), encoding="utf-8")

    print(f"[INFO] 使用 tmux 会话: {session}（左=PeerA / 右=PeerB）")
    print(f"[TIP] 另开终端可随时 `tmux attach -t {session}` 旁观/插话")

    # 启动交互式 CLI
    CLAUDE_I_CMD=os.environ.get("CLAUDE_I_CMD")
    CODEX_I_CMD=os.environ.get("CODEX_I_CMD")
    if not CLAUDE_I_CMD or not CODEX_I_CMD:
        print("[FATAL] 请设置 CLAUDE_I_CMD 与 CODEX_I_CMD，例如：\n"
              "export CLAUDE_I_CMD='claude-code chat'\nexport CODEX_I_CMD='codex chat'"); 
        raise SystemExit(1)
    tmux_start_interactive(left, CLAUDE_I_CMD)
    tmux_start_interactive(right, CODEX_I_CMD)
    time.sleep(1.0)

    # 注入 SYSTEM（角色核心 + 人格 + 策略）
    sysA = weave_system(home, "peerA"); sysB = weave_system(home, "peerB")
    tmux_paste(left,  f"[SYSTEM]\n{sysA}\n")
    tmux_paste(right, f"[SYSTEM]\n{sysB}\n")
    print("[BOOT] 已注入 SYSTEM 到两位长连接会话")

    # 监听可热更的文件
    watch_files = [
        settings/"roles.yaml", settings/"policies.yaml", settings/"traits.yaml",
        prompts/"peerA.core.txt", prompts/"peerB.core.txt", prompts/"shared.guardrails.txt",
        personas/"peerA.persona.txt", personas/"peerB.persona.txt",
    ]
    mtimes = snapshot_mtime(watch_files)

    # 初始愿景
    print("\n请输入你的“模糊愿景”或指示（单行）。Ctrl+C 退出。")
    initial = input("> REQUIREMENT: ").strip()
    if not initial: 
        print("[FATAL] 未输入。"); raise SystemExit(1)

    phase = "discovery"
    ctx = context_blob(policies, phase)

    first = (f"<TO_USER>{initial}</TO_USER>\n"
             f"<TO_PEER>\n"
             f"type: CLAIM\nintent: {phase}\n"
             f"tasks:\n  - desc: '组织问题空间、PRD草案与最小里程碑；提出首批验证实验'\n"
             f"    constraints: {{ max_diff_lines: {policies.get('patch_queue',{}).get('max_diff_lines',150)} }}\n"
             f"</TO_PEER>\n"
             f"<SYSTEM_NOTES>agent: {leader}; role: leader</SYSTEM_NOTES>\n")

    # 第一轮先发 leader
    if leader=="peera":
        exchange_once(home, left, right, first, ctx, "PeerA", policies, phase)
    else:
        exchange_once(home, right, left, first, ctx, "PeerB", policies, phase)

    rounds = 3
    for i in range(rounds):
        # 热更新 system
        if changed_files(mtimes, watch_files):
            print("[SYSTEM] 侦测到 persona/策略变更，刷新注入 …")
            sysA = weave_system(home, "peerA"); sysB = weave_system(home, "peerB")
            tmux_paste(left,  f"[SYSTEM]\n{sysA}\n")
            tmux_paste(right, f"[SYSTEM]\n{sysB}\n")
            mtimes = snapshot_mtime(watch_files)

        # A -> B
        exchange_once(home, left, right, "<TO_PEER>type: CLAIM\nintent: implement\n</TO_PEER>", ctx, "PeerA", policies, phase)
        # B -> A
        exchange_once(home, right, left, "<TO_PEER>type: CLAIM\nintent: review|fix\n</TO_PEER>", ctx, "PeerB", policies, phase)

        print("\n[操作] 回车继续；输入 `u: ...` 广播给两位；输入 `/refresh` 刷新 SYSTEM；输入 `q` 退出。")
        line = input("> ").strip()
        if line.lower()=="q": break
        if line == "/refresh":
            sysA = weave_system(home, "peerA"); sysB = weave_system(home, "peerB")
            tmux_paste(left,  f"[SYSTEM]\n{sysA}\n")
            tmux_paste(right, f"[SYSTEM]\n{sysB}\n")
            print("[SYSTEM] 已刷新。"); continue
        if line.startswith("u:"):
            msg=line[2:].strip()
            tmux_paste(left,  f"[INPUT]\n<TO_USER>{msg}</TO_USER>\n")
            tmux_paste(right, f"[INPUT]\n<TO_USER>{msg}</TO_USER>\n")

        if i==0:
            phase="implement"; ctx=context_blob(policies, phase)

    print("\n[END] 最近提交：")
    run("git --no-pager log -n 5 --oneline")
    print("Ledger:", (home/"state/ledger.jsonl"))
    print(f"[TIP] 你可继续 `tmux attach -t {session}` 与两位 AI 互动。")
