import test from "node:test";
import assert from "node:assert/strict";

import {
  buildDesktopPetDaemonUrl,
  buildDesktopPetLaunchUrl,
  pickLaunchToken,
} from "./desktopPetLaunch.ts";

test("buildDesktopPetDaemonUrl trims trailing slash", () => {
  assert.equal(
    buildDesktopPetDaemonUrl("https://cccc.example.com/"),
    "https://cccc.example.com"
  );
});

test("buildDesktopPetLaunchUrl encodes query params", () => {
  assert.equal(
    buildDesktopPetLaunchUrl({
      daemonUrl: "https://cccc.example.com/ui",
      token: "tok en/with?chars",
      groupId: "g_demo",
    }),
    "cccc-pet://launch?daemon_url=https%3A%2F%2Fcccc.example.com%2Fui&token=tok%20en%2Fwith%3Fchars&group_id=g_demo"
  );
});

test("pickLaunchToken prefers scoped token before admin", () => {
  const token = pickLaunchToken(
    [
      { is_admin: false, allowed_groups: ["g_demo"], token_id: "scoped-1" },
      { is_admin: true, allowed_groups: [], token_id: "admin-1" },
    ],
    "g_demo"
  );
  assert.deepEqual(token, {
    is_admin: false,
    allowed_groups: ["g_demo"],
    token_id: "scoped-1",
  });
});

test("pickLaunchToken falls back to admin when no scoped token matches", () => {
  const token = pickLaunchToken(
    [
      { is_admin: false, allowed_groups: ["g_other"], token_id: "scoped-1" },
      { is_admin: true, allowed_groups: [], token_id: "admin-1" },
    ],
    "g_demo"
  );
  assert.deepEqual(token, {
    is_admin: true,
    allowed_groups: [],
    token_id: "admin-1",
  });
});
