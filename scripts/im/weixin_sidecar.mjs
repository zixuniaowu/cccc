#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import readline from "node:readline";
import { pathToFileURL } from "node:url";

function emit(event, payload = {}) {
  process.stdout.write(`${JSON.stringify({ type: "event", event, ...payload })}\n`);
}

function emitLog(message) {
  emit("log", { message: String(message || "") });
}

function parseArgs(argv) {
  const out = {
    mode: "bridge",
    stateFile: "",
  };
  for (let i = 2; i < argv.length; i += 1) {
    const part = String(argv[i] || "");
    if (part === "--login") out.mode = "login";
    else if (part === "--logout") out.mode = "logout";
    else if (part === "--state-file" && i + 1 < argv.length) {
      out.stateFile = String(argv[i + 1] || "");
      i += 1;
    }
  }
  return out;
}

function writeState(stateFile, patch) {
  if (!stateFile) return;
  let current = {};
  try {
    current = JSON.parse(fs.readFileSync(stateFile, "utf8"));
  } catch {
    current = {};
  }
  const next = {
    ...current,
    ...patch,
    updated_at: new Date().toISOString(),
  };
  fs.mkdirSync(path.dirname(stateFile), { recursive: true });
  fs.writeFileSync(stateFile, JSON.stringify(next, null, 2), "utf8");
}

async function loadWeixinInternalModule(packageEntryUrl, relCandidates) {
  const entryPath = new URL(packageEntryUrl).pathname;
  const packageRoot = path.resolve(path.dirname(entryPath), "..");
  for (const rel of relCandidates) {
    const abs = path.resolve(packageRoot, rel);
    if (!fs.existsSync(abs)) continue;
    try {
      return await import(pathToFileURL(abs).href);
    } catch {
      continue;
    }
  }
  return null;
}

async function loadWeixinBundledInternals(packageEntryUrl) {
  if (!packageEntryUrl) return null;
  try {
    const entryPath = new URL(packageEntryUrl).pathname;
    const source = fs.readFileSync(entryPath, "utf8");
    const tempPath = path.join(
      os.tmpdir(),
      `cccc-weixin-sdk-internals-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.mjs`,
    );
    const extra = "\nexport { startWeixinLoginWithQr, waitForWeixinLogin, normalizeAccountId, saveWeixinAccount, registerWeixinAccountId };\nexport const DEFAULT_ILINK_BOT_TYPE = '3';\n";
    fs.writeFileSync(tempPath, source + extra, "utf8");
    return await import(pathToFileURL(tempPath).href);
  } catch {
    return null;
  }
}

async function main() {
  const args = parseArgs(process.argv);
  let sdk;
  let sdkEntryUrl = "";
  try {
    sdkEntryUrl = await import.meta.resolve("weixin-agent-sdk");
    sdk = await import("weixin-agent-sdk");
  } catch (error) {
    writeState(args.stateFile, {
      status: "error",
      logged_in: false,
      error: "failed to import weixin-agent-sdk",
    });
    emit("error", {
      message:
        "failed to import weixin-agent-sdk; install it first, e.g. npm install weixin-agent-sdk",
    });
    process.exit(1);
  }

  const { isLoggedIn, start, login, logout } = sdk;
  if (typeof isLoggedIn !== "function") {
    writeState(args.stateFile, {
      status: "error",
      logged_in: false,
      error: "weixin-agent-sdk exports are incomplete",
    });
    emit("error", { message: "weixin-agent-sdk exports are incomplete" });
    process.exit(1);
  }

  if (args.mode === "logout") {
    if (typeof logout === "function") {
      logout({ log: emitLog });
    }
    writeState(args.stateFile, {
      status: "logged_out",
      logged_in: false,
      account_id: "",
      qr_ascii: "",
      error: "",
    });
    emit("ready", { logged_out: true });
    return;
  }

  if (args.mode === "login") {
    if (typeof login !== "function") {
      writeState(args.stateFile, {
        status: "error",
        logged_in: false,
        error: "weixin-agent-sdk login() is unavailable",
      });
      emit("error", { message: "weixin-agent-sdk login() is unavailable" });
      process.exit(1);
    }

    writeState(args.stateFile, {
      status: "starting_login",
      logged_in: false,
      qrcode_url: "",
      qr_ascii: "",
      error: "",
    });

    try {
      const loginQrModule = sdkEntryUrl
        ? await loadWeixinInternalModule(sdkEntryUrl, [
            "dist/src/auth/login-qr.mjs",
            "dist/auth/login-qr.mjs",
            "dist/src/auth/login-qr.js",
            "dist/auth/login-qr.js",
          ])
        : null;
      const accountsModule = sdkEntryUrl
        ? await loadWeixinInternalModule(sdkEntryUrl, [
            "dist/src/auth/accounts.mjs",
            "dist/auth/accounts.mjs",
            "dist/src/auth/accounts.js",
            "dist/auth/accounts.js",
          ])
        : null;
      const bundledInternals =
        !loginQrModule || !accountsModule ? await loadWeixinBundledInternals(sdkEntryUrl) : null;

      let accountId = "";
      const loginInternals = loginQrModule || bundledInternals;
      const accountInternals = accountsModule || bundledInternals;
      if (loginInternals && accountInternals) {
        const startWeixinLoginWithQr = loginInternals.startWeixinLoginWithQr;
        const waitForWeixinLogin = loginInternals.waitForWeixinLogin;
        const DEFAULT_ILINK_BOT_TYPE = loginInternals.DEFAULT_ILINK_BOT_TYPE || "3";
        const normalizeAccountId = accountInternals.normalizeAccountId;
        const saveWeixinAccount = accountInternals.saveWeixinAccount;
        const registerWeixinAccountId = accountInternals.registerWeixinAccountId;
        if (
          typeof startWeixinLoginWithQr === "function" &&
          typeof waitForWeixinLogin === "function" &&
          typeof normalizeAccountId === "function" &&
          typeof saveWeixinAccount === "function" &&
          typeof registerWeixinAccountId === "function"
        ) {
          const startResult = await startWeixinLoginWithQr({
            apiBaseUrl: "https://ilinkai.weixin.qq.com",
            botType: DEFAULT_ILINK_BOT_TYPE,
          });
          if (!startResult?.qrcodeUrl || !startResult?.sessionKey) {
            throw new Error(startResult?.message || "failed to obtain weixin qrcode");
          }
          writeState(args.stateFile, {
            status: "waiting_scan",
            logged_in: false,
            qrcode_url: String(startResult.qrcodeUrl || ""),
            qr_ascii: "",
            error: "",
          });
          emit("qr_ready", { qrcode_url: String(startResult.qrcodeUrl || "") });

          try {
            const qrterm = await import("qrcode-terminal");
            await new Promise((resolve) => {
              qrterm.default.generate(String(startResult.qrcodeUrl || ""), { small: true }, (qr) => {
                writeState(args.stateFile, {
                  status: "waiting_scan",
                  logged_in: false,
                  qrcode_url: String(startResult.qrcodeUrl || ""),
                  qr_ascii: String(qr || ""),
                  error: "",
                });
                emit("qr_ascii", { qr_ascii: String(qr || "") });
                resolve();
              });
            });
          } catch {
            // Image URL is enough for Web UI.
          }

          const waitResult = await waitForWeixinLogin({
            sessionKey: startResult.sessionKey,
            apiBaseUrl: "https://ilinkai.weixin.qq.com",
            timeoutMs: 480000,
            botType: DEFAULT_ILINK_BOT_TYPE,
          });
          if (!waitResult?.connected || !waitResult?.botToken || !waitResult?.accountId) {
            throw new Error(waitResult?.message || "weixin login failed");
          }
          const normalizedId = normalizeAccountId(waitResult.accountId);
          saveWeixinAccount(normalizedId, {
            token: waitResult.botToken,
            baseUrl: waitResult.baseUrl,
            userId: waitResult.userId,
          });
          registerWeixinAccountId(normalizedId);
          accountId = String(normalizedId || "");
        } else {
          throw new Error("weixin internal login helpers unavailable");
        }
      } else {
        const originalConsoleLog = console.log;
        console.log = (...parts) => {
          const text = parts.map((part) => String(part ?? "")).join(" ").trimEnd();
          if (text.includes("█") || text.includes("▀") || text.includes("▄")) {
            writeState(args.stateFile, {
              status: "waiting_scan",
              logged_in: false,
              qrcode_url: "",
              qr_ascii: text,
              error: "",
            });
            emit("qr_ascii", { qr_ascii: text });
          } else if (text) {
            emitLog(text);
          }
          originalConsoleLog(...parts);
        };
        try {
          accountId = await login({ log: emitLog });
        } finally {
          console.log = originalConsoleLog;
        }
      }

      writeState(args.stateFile, {
        status: "logged_in",
        logged_in: true,
        account_id: String(accountId || ""),
        qrcode_url: "",
        qr_ascii: "",
        error: "",
      });
      emit("ready", { account_id: String(accountId || "") });
      return;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      writeState(args.stateFile, {
        status: "error",
        logged_in: false,
        qrcode_url: "",
        error: message,
      });
      emit("error", { message });
      process.exit(1);
    }
  }

  if (typeof start !== "function") {
    writeState(args.stateFile, {
      status: "error",
      logged_in: false,
      error: "weixin-agent-sdk start() is unavailable",
    });
    emit("error", { message: "weixin-agent-sdk start() is unavailable" });
    process.exit(1);
  }

  if (!isLoggedIn()) {
    writeState(args.stateFile, {
      status: "not_logged_in",
      logged_in: false,
      account_id: "",
      qrcode_url: "",
      error: "",
    });
    emit("error", {
      message:
        "weixin is not logged in; run a host-side login flow first (for example: npx weixin-acp login)",
    });
    process.exit(1);
  }

  const accountId = String(process.env.CCCC_IM_WEIXIN_ACCOUNT_ID || "").trim() || undefined;
  const pendingReplies = new Map();
  const abortController = new AbortController();
  writeState(args.stateFile, {
    status: "running",
    logged_in: true,
    account_id: accountId || "",
    qrcode_url: "",
    error: "",
  });

  const rl = readline.createInterface({
    input: process.stdin,
    crlfDelay: Infinity,
  });

  rl.on("line", (line) => {
    const text = String(line || "").trim();
    if (!text) return;
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      emitLog(`ignored non-json command: ${text.slice(0, 200)}`);
      return;
    }
    if (!payload || payload.type !== "cmd") return;

    if (payload.cmd === "shutdown") {
      abortController.abort();
      return;
    }

    if (payload.cmd !== "reply") return;
    const requestId = String(payload.request_id || "").trim();
    if (!requestId) return;
    const pending = pendingReplies.get(requestId);
    if (!pending) {
      emitLog(`missing pending reply handle for request_id=${requestId}`);
      return;
    }
    pendingReplies.delete(requestId);
    pending.resolve({
      text: String(payload.text || ""),
    });
  });

  process.on("SIGINT", () => abortController.abort());
  process.on("SIGTERM", () => abortController.abort());

  const agent = {
    async chat(request) {
      const conversationId = String(request?.conversationId || "").trim();
      const requestId = `wxreq_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
      const media = request?.media && typeof request.media === "object" ? request.media : undefined;

      const response = await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          pendingReplies.delete(requestId);
          reject(new Error("cccc weixin reply timeout"));
        }, 5 * 60 * 1000);
        pendingReplies.set(requestId, {
          resolve: (value) => {
            clearTimeout(timeout);
            resolve(value);
          },
          reject: (error) => {
            clearTimeout(timeout);
            reject(error);
          },
        });
        emit("message", {
          chat_id: conversationId,
          chat_title: conversationId,
          chat_type: "p2p",
          from_user: conversationId,
          request_id: requestId,
          message_id: requestId,
          text: String(request?.text || ""),
          timestamp: Date.now() / 1000,
          attachment: media
            ? {
                type: media.type,
                kind: media.type === "image" ? "image" : "file",
                file_path: media.filePath,
                mime_type: media.mimeType,
                file_name: media.fileName || "",
                bytes: (() => {
                  try {
                    return fs.statSync(media.filePath).size;
                  } catch {
                    return 0;
                  }
                })(),
                provider: "weixin",
              }
            : undefined,
        });
      });
      return response && typeof response === "object" ? response : { text: "" };
    },
  };

  emit("ready", { account_id: accountId || "" });

  try {
    await start(agent, {
      accountId,
      abortSignal: abortController.signal,
      log: emitLog,
    });
  } catch (error) {
    writeState(args.stateFile, {
      status: "error",
      logged_in: isLoggedIn(),
      account_id: accountId || "",
      qrcode_url: "",
      error: error instanceof Error ? error.message : String(error),
    });
    emit("error", { message: error instanceof Error ? error.message : String(error) });
    process.exit(1);
  } finally {
    rl.close();
  }
}

main().catch((error) => {
  emit("error", { message: error instanceof Error ? error.message : String(error) });
  process.exit(1);
});
