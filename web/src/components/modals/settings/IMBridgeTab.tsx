// IMBridgeTab configures IM bridge settings.
import { IMStatus, IMPlatform } from "../../../types";
import { inputClass, labelClass, primaryButtonClass, cardClass } from "./types";

interface IMBridgeTabProps {
  isDark: boolean;
  groupId?: string; // Reserved for future use.
  imStatus: IMStatus | null;
  imPlatform: IMPlatform;
  setImPlatform: (v: IMPlatform) => void;
  imBotTokenEnv: string;
  setImBotTokenEnv: (v: string) => void;
  imAppTokenEnv: string;
  setImAppTokenEnv: (v: string) => void;
  // Feishu fields
  imFeishuDomain: string;
  setImFeishuDomain: (v: string) => void;
  imFeishuAppId: string;
  setImFeishuAppId: (v: string) => void;
  imFeishuAppSecret: string;
  setImFeishuAppSecret: (v: string) => void;
  // DingTalk fields
  imDingtalkAppKey: string;
  setImDingtalkAppKey: (v: string) => void;
  imDingtalkAppSecret: string;
  setImDingtalkAppSecret: (v: string) => void;
  imDingtalkRobotCode: string;
  setImDingtalkRobotCode: (v: string) => void;
  // Actions
  imBusy: boolean;
  onSaveConfig: () => void;
  onRemoveConfig: () => void;
  onStartBridge: () => void;
  onStopBridge: () => void;
}

export function IMBridgeTab({
  isDark,
  groupId: _groupId,
  imStatus,
  imPlatform,
  setImPlatform,
  imBotTokenEnv,
  setImBotTokenEnv,
  imAppTokenEnv,
  setImAppTokenEnv,
  imFeishuDomain,
  setImFeishuDomain,
  imFeishuAppId,
  setImFeishuAppId,
  imFeishuAppSecret,
  setImFeishuAppSecret,
  imDingtalkAppKey,
  setImDingtalkAppKey,
  imDingtalkAppSecret,
  setImDingtalkAppSecret,
  imDingtalkRobotCode,
  setImDingtalkRobotCode,
  imBusy,
  onSaveConfig,
  onRemoveConfig,
  onStartBridge,
  onStopBridge,
}: IMBridgeTabProps) {
  const getBotTokenLabel = () => {
    switch (imPlatform) {
      case "telegram": return "Bot Token (token or env var)";
      case "slack": return "Bot Token (xoxb- or env var)";
      case "discord": return "Bot Token (token or env var)";
      default: return "Bot Token";
    }
  };

  const getBotTokenPlaceholder = () => {
    switch (imPlatform) {
      case "telegram": return "TELEGRAM_BOT_TOKEN (or 123456:ABC...)";
      case "slack": return "SLACK_BOT_TOKEN (or xoxb-...)";
      case "discord": return "DISCORD_BOT_TOKEN (or <token>)";
      default: return "";
    }
  };

  const canSaveIM = () => {
    if (imPlatform === "feishu") {
      return !!imFeishuAppId && !!imFeishuAppSecret;
    }
    if (imPlatform === "dingtalk") {
      return !!imDingtalkAppKey && !!imDingtalkAppSecret;
    }
    if (!imBotTokenEnv) return false;
    if (imPlatform === "slack" && !imAppTokenEnv) return false;
    return true;
  };

  const needsBotToken = imPlatform === "telegram" || imPlatform === "slack" || imPlatform === "discord";

  return (
    <div className="space-y-4">
      <div>
        <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>IM Bridge</h3>
        <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
          Connect this group to Telegram, Slack, Discord, Feishu/Lark, or DingTalk.
        </p>
      </div>

      {/* Status */}
      {imStatus && (
        <div className={cardClass(isDark)}>
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${imStatus.running ? "bg-emerald-500" : "bg-gray-400"}`} />
            <span className={`text-sm ${isDark ? "text-slate-300" : "text-gray-700"}`}>
              {imStatus.running ? "Running" : "Stopped"}
            </span>
            {imStatus.running && imStatus.pid && (
              <span className={`text-xs ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                (PID: {imStatus.pid})
              </span>
            )}
          </div>
          {imStatus.configured && (
            <div className={`text-xs mt-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>
              Platform: {imStatus.platform} • Subscribers: {imStatus.subscribers}
            </div>
          )}
        </div>
      )}

      {/* Configuration */}
      <div className="space-y-3">
        <div>
          <label className={labelClass(isDark)}>Platform</label>
          <select
            value={imPlatform}
            onChange={(e) => setImPlatform(e.target.value as IMPlatform)}
            className={inputClass(isDark)}
          >
            <option value="telegram">Telegram</option>
            <option value="slack">Slack</option>
            <option value="discord">Discord</option>
            <option value="feishu">Feishu/Lark</option>
            <option value="dingtalk">DingTalk</option>
          </select>
        </div>

        {/* Bot Token (Telegram/Slack/Discord) */}
        {needsBotToken && (
          <div>
            <label className={labelClass(isDark)}>{getBotTokenLabel()}</label>
            <input
              type="text"
              value={imBotTokenEnv}
              onChange={(e) => setImBotTokenEnv(e.target.value)}
              placeholder={getBotTokenPlaceholder()}
              className={`${inputClass(isDark)} placeholder:${isDark ? "text-slate-600" : "text-gray-400"}`}
            />
            <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
              {imPlatform === "slack"
                ? "Paste xoxb-… token or an env var name; required for outbound messages."
                : "Paste the bot token or an env var name; required for bot authentication."}
            </p>
          </div>
        )}

        {/* App Token (Slack only) */}
        {imPlatform === "slack" && (
          <div>
            <label className={labelClass(isDark)}>App Token (xapp- or env var)</label>
            <input
              type="text"
              value={imAppTokenEnv}
              onChange={(e) => setImAppTokenEnv(e.target.value)}
              placeholder="SLACK_APP_TOKEN (or xapp-...)"
              className={`${inputClass(isDark)} placeholder:${isDark ? "text-slate-600" : "text-gray-400"}`}
            />
            <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
              Optional; needed for inbound messages (Socket Mode).
            </p>
          </div>
        )}

        {/* Feishu fields */}
        {imPlatform === "feishu" && (
          <>
            <div>
              <label className={labelClass(isDark)}>API Region</label>
              <select
                value={imFeishuDomain}
                onChange={(e) => setImFeishuDomain(e.target.value)}
                className={inputClass(isDark)}
              >
                <option value="https://open.feishu.cn">Feishu (CN) • open.feishu.cn</option>
                <option value="https://open.larkoffice.com">Lark (Global) • open.larkoffice.com</option>
              </select>
              <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                Feishu and Lark share the same APIs but use different domains. Pick the one where your app was created.
              </p>
              <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                Inbound streaming requires the Python package <code>lark-oapi</code> on the host running CCCC.
              </p>
            </div>
            <div>
              <label className={labelClass(isDark)}>App ID</label>
              <input
                type="text"
                value={imFeishuAppId}
                onChange={(e) => setImFeishuAppId(e.target.value)}
                placeholder="FEISHU_APP_ID (or cli_xxx...)"
                className={`${inputClass(isDark)} placeholder:${isDark ? "text-slate-600" : "text-gray-400"}`}
              />
              <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                App ID value or an env var name.
              </p>
            </div>
            <div>
              <label className={labelClass(isDark)}>App Secret</label>
              <input
                type="password"
                value={imFeishuAppSecret}
                onChange={(e) => setImFeishuAppSecret(e.target.value)}
                placeholder="FEISHU_APP_SECRET (or secret)"
                className={`${inputClass(isDark)} placeholder:${isDark ? "text-slate-600" : "text-gray-400"}`}
              />
              <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                App Secret value or an env var name.
              </p>
            </div>
          </>
        )}

        {/* DingTalk fields */}
        {imPlatform === "dingtalk" && (
          <>
            <div>
              <label className={labelClass(isDark)}>App Key</label>
              <input
                type="text"
                value={imDingtalkAppKey}
                onChange={(e) => setImDingtalkAppKey(e.target.value)}
                placeholder="DINGTALK_APP_KEY (or key)"
                className={`${inputClass(isDark)} placeholder:${isDark ? "text-slate-600" : "text-gray-400"}`}
              />
              <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                App Key value or an env var name.
              </p>
            </div>
            <div>
              <label className={labelClass(isDark)}>App Secret</label>
              <input
                type="password"
                value={imDingtalkAppSecret}
                onChange={(e) => setImDingtalkAppSecret(e.target.value)}
                placeholder="DINGTALK_APP_SECRET (or secret)"
                className={`${inputClass(isDark)} placeholder:${isDark ? "text-slate-600" : "text-gray-400"}`}
              />
              <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                App Secret value or an env var name.
              </p>
            </div>
            <div>
              <label className={labelClass(isDark)}>Robot Code (optional)</label>
              <input
                type="text"
                value={imDingtalkRobotCode}
                onChange={(e) => setImDingtalkRobotCode(e.target.value)}
                placeholder="DINGTALK_ROBOT_CODE (or robotCode)"
                className={`${inputClass(isDark)} placeholder:${isDark ? "text-slate-600" : "text-gray-400"}`}
              />
              <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                Optional; used when the session webhook is unavailable or expired.
              </p>
              <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                Inbound streaming requires the Python package <code>dingtalk-stream</code> on the host running CCCC.
              </p>
            </div>
          </>
        )}
      </div>

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={onSaveConfig}
          disabled={imBusy || !canSaveIM()}
          className={primaryButtonClass(imBusy)}
        >
          {imBusy ? "Saving..." : "Save Config"}
        </button>

        {imStatus?.configured && (
          <>
            {imStatus.running ? (
              <button
                onClick={onStopBridge}
                disabled={imBusy}
                className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium ${
                  isDark
                    ? "bg-red-900/50 hover:bg-red-800/50 text-red-300"
                    : "bg-red-100 hover:bg-red-200 text-red-700"
                } disabled:opacity-50`}
              >
                Stop Bridge
              </button>
            ) : (
              <button
                onClick={onStartBridge}
                disabled={imBusy}
                className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium ${
                  isDark
                    ? "bg-blue-900/50 hover:bg-blue-800/50 text-blue-300"
                    : "bg-blue-100 hover:bg-blue-200 text-blue-700"
                } disabled:opacity-50`}
              >
                Start Bridge
              </button>
            )}

            <button
              onClick={onRemoveConfig}
              disabled={imBusy}
              className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium ${
                isDark
                  ? "bg-slate-800 hover:bg-slate-700 text-slate-300"
                  : "bg-gray-200 hover:bg-gray-300 text-gray-700"
              } disabled:opacity-50`}
            >
              Remove Config
            </button>
          </>
        )}
      </div>

      {/* Help */}
      <div className={`text-xs space-y-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
        <p>To use IM Bridge:</p>
        <ol className="list-decimal list-inside space-y-0.5 ml-2">
          <li>Create a bot on your IM platform</li>
          <li>Set the token(s) as environment variable(s)</li>
          <li>Save the config and start the bridge</li>
          <li>In your IM chat, send /subscribe to receive messages</li>
        </ol>
      </div>
    </div>
  );
}
