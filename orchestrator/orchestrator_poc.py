# file: orchestrator/orchestrator_poc.py
import os, re, subprocess, tempfile, json, sys, textwrap

CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "echo")
CODEX_CMD  = os.environ.get("CODEX_CMD",  "echo")
TEST_CMD   = os.environ.get("TEST_CMD",   "echo 'no tests'")
REPO_DIR   = os.getcwd()

A_SYS = open(os.path.join("prompts","claude.system.txt")).read() if os.path.exists("prompts/claude.system.txt") else "You are Peer A."
B_SYS = open(os.path.join("prompts","peer.system.txt")).read()   if os.path.exists("prompts/peer.system.txt")   else "You are Peer B."

PATCH_RE = re.compile(r"```patch\s*\*{0,3}\s*PATCH?([\s\S]*?)\*{0,3}\s*END\s*PATCH\s*```|```patch([\s\S]*?)```", re.I)

def run_agent(cmd, system_prompt, user_block, context=""):
    payload = f"[SYSTEM]\n{system_prompt}\n\n[CONTEXT]\n{context}\n\n[INPUT]\n{user_block}\n"
    p = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=REPO_DIR)
    out, err = p.communicate(payload)
    if p.returncode != 0:
        print(f"[orchestrator] agent error: {err[:400]}", file=sys.stderr)
    return out

def parse_sections(text):
    def take(tag):
        m = re.search(fr"<{tag}>([\s\S]*?)</{tag}>", text)
        return (m.group(1).strip() if m else "")
    return {
        "to_user": take("TO_USER"),
        "to_peer": take("TO_PEER"),
        "notes": take("SYSTEM_NOTES"),
        "patch": extract_patch(text)
    }

def extract_patch(text):
    m = PATCH_RE.search(text)
    if not m: return ""
    body = m.group(1) or m.group(2) or ""
    body = body.strip()
    if not body.startswith("*** PATCH"):
        body = f"*** PATCH\n{body}\n*** END PATCH"
    return body

def git_apply_check(patch):
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(patch)
        path = f.name
    r = subprocess.run(["git","apply","--check",path], cwd=REPO_DIR)
    return r.returncode == 0, path

def git_apply(patch):
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(patch)
        path = f.name
    r = subprocess.run(["git","apply",path], cwd=REPO_DIR)
    return r.returncode == 0

def run_tests():
    return subprocess.call(TEST_CMD, shell=True, cwd=REPO_DIR) == 0

def summarize_for_peer(sections):
    # 控制成本：只把 <TO_PEER> 送给对方；<TO_USER> 用于展示。
    return sections["to_peer"]

def pretty(title, content):
    bar = "="*8
    return f"\n{bar} {title} {bar}\n{content.strip()}\n"

def advance_once(agent_name, cmd, sys_prompt, user_msg, context=""):
    out = run_agent(cmd, sys_prompt, user_msg, context)
    secs = parse_sections(out)
    if secs["to_user"]:
        print(pretty(f"{agent_name} → USER", secs["to_user"]))
    if secs["patch"]:
        ok, tmp = git_apply_check(secs["patch"])
        if not ok:
            print(pretty("PATCH PRECHECK FAILED", "patch didn't apply cleanly; sending back to leader as COUNTER/EVIDENCE request"))
        else:
            applied = git_apply(secs["patch"])
            if applied:
                print(pretty("PATCH APPLIED", "patch applied"))
                tests_ok = run_tests()
                print(pretty("TESTS", "PASS" if tests_ok else "FAIL"))
                if not tests_ok:
                    # 把失败日志最小化回送
                    fail_msg = "<TO_PEER>\n" + textwrap.dedent(f"""\
                        type: EVIDENCE
                        intent: fix
                        refs: ["TEST:failed"]
                        tasks:
                          - desc: "测试失败，生成最小修复补丁"
                        """) + "\n</TO_PEER>"
                    secs["to_peer"] = fail_msg
            else:
                print(pretty("PATCH APPLY", "failed"))
    return secs

def main():
    print("DuetFlow PoC started. Paste your requirement (single line). Ctrl+C to exit.")
    initial = input("> REQUIREMENT: ").strip()
    if not initial:
        print("no requirement, exit"); return
    context = ""
    # 第一轮：A 先发起（队长），把 <TO_PEER> 投递给 B
    a = advance_once("PeerA", CLAUDE_CMD, A_SYS, f"<TO_USER>{initial}</TO_USER>\n<TO_PEER>type: CLAIM\nintent: plan\n</TO_PEER>", context)
    b_in = summarize_for_peer(a)
    if not b_in:
        print("A 没有给 B 的交接，结束。"); return
    b = advance_once("PeerB", CODEX_CMD, B_SYS, b_in, context)
    # 继续一回合：把 B 的 <TO_PEER> 送回 A
    a_in2 = summarize_for_peer(b)
    if a_in2:
        _ = advance_once("PeerA", CLAUDE_CMD, A_SYS, a_in2, context)
    print("\n[PoC] 结束：请查看补丁与测试结果。")

if __name__ == "__main__":
    main()
