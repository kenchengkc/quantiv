import assert from "node:assert/strict";
import test from "node:test";

import {
  easternDate,
  isNormalRefreshRun,
  scheduledMode,
  watchdogRunState,
} from "./index.js";

test("dispatches at 02:00 Eastern in EDT", () => {
  assert.equal(scheduledMode(new Date("2026-09-28T06:00:00Z")), "dispatch");
  assert.equal(scheduledMode(new Date("2026-09-28T07:00:00Z")), "noop");
});

test("dispatches at 02:00 Eastern in EST", () => {
  assert.equal(scheduledMode(new Date("2026-12-28T07:00:00Z")), "dispatch");
  assert.equal(scheduledMode(new Date("2026-12-28T06:00:00Z")), "noop");
});

test("watchdog runs ten minutes after the Eastern dispatch", () => {
  assert.equal(scheduledMode(new Date("2026-09-28T06:10:00Z")), "watchdog");
  assert.equal(scheduledMode(new Date("2026-12-28T07:10:00Z")), "watchdog");
});

test("spring-forward day substitutes 03:00 for nonexistent 02:00", () => {
  assert.equal(scheduledMode(new Date("2027-03-14T07:00:00Z")), "dispatch");
  assert.equal(scheduledMode(new Date("2027-03-14T07:10:00Z")), "watchdog");
});

test("run matching uses Eastern calendar date and ignores recovery runs", () => {
  const localDate = easternDate(new Date("2026-09-28T06:00:00Z"));
  assert.equal(
    isNormalRefreshRun(
      {
        event: "workflow_dispatch",
        created_at: "2026-09-28T06:00:02Z",
        display_title: "Daily data refresh [normal]",
      },
      localDate,
    ),
    true,
  );
  assert.equal(
    isNormalRefreshRun(
      {
        event: "workflow_dispatch",
        created_at: "2026-09-28T06:00:02Z",
        display_title: "Daily data refresh [provider-free-recovery]",
      },
      localDate,
    ),
    false,
  );
});


test("watchdog distinguishes missing start from failed completion", () => {
  assert.equal(watchdogRunState({ status: "queued", conclusion: null }), "queued");
  assert.equal(
    watchdogRunState({ status: "completed", conclusion: "failure" }),
    "failed",
  );
  assert.equal(
    watchdogRunState({ status: "completed", conclusion: "success" }),
    "started",
  );
  assert.equal(watchdogRunState({ status: "in_progress", conclusion: null }), "started");
});
