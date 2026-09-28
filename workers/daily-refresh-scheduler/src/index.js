const EASTERN = "America/New_York";
const RECOVERY_MARKER = "provider-free-recovery";

function partsFor(date) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: EASTERN,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return Object.fromEntries(
    formatter
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
}

export function easternDate(date) {
  const parts = partsFor(date);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function springForwardSubstitute(date, minute) {
  const current = partsFor(date);
  if (current.hour !== "03" || current.minute !== minute) return false;
  const previous = partsFor(new Date(date.getTime() - 60 * 60 * 1000));
  return (
    previous.year === current.year &&
    previous.month === current.month &&
    previous.day === current.day &&
    previous.hour === "01" &&
    previous.minute === minute
  );
}

export function scheduledMode(date) {
  const parts = partsFor(date);
  if (parts.hour === "02" && parts.minute === "00") return "dispatch";
  if (parts.hour === "02" && parts.minute === "10") return "watchdog";
  // 02:xx does not exist on the spring-forward Sunday. The 03:xx invocation
  // immediately after the one-hour local-time gap substitutes for it.
  if (springForwardSubstitute(date, "00")) return "dispatch";
  if (springForwardSubstitute(date, "10")) return "watchdog";
  return "noop";
}

function githubHeaders(token) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    "User-Agent": "quantiv-daily-refresh-scheduler",
    "X-GitHub-Api-Version": "2026-03-10",
  };
}

function workflowUrl(env, suffix = "") {
  const workflow = encodeURIComponent(env.GITHUB_WORKFLOW || "data-refresh.yml");
  return `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${workflow}${suffix}`;
}

async function githubRequest(env, url, init = {}) {
  const response = await fetch(url, {
    ...init,
    headers: {
      ...githubHeaders(env.GITHUB_TOKEN),
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `GitHub API ${init.method || "GET"} ${url} failed: ${response.status} ${body.slice(0, 300)}`,
    );
  }
  return response;
}

export function isNormalRefreshRun(run, localDate) {
  if (!["schedule", "workflow_dispatch"].includes(run.event)) return false;
  const title = String(run.display_title || run.name || "").toLowerCase();
  if (title.includes(RECOVERY_MARKER)) return false;
  if (!run.created_at) return false;
  return easternDate(new Date(run.created_at)) === localDate;
}

async function normalRunsForDate(env, localDate) {
  const response = await githubRequest(
    env,
    workflowUrl(env, "/runs?per_page=30"),
  );
  const payload = await response.json();
  return (payload.workflow_runs || []).filter((run) =>
    isNormalRefreshRun(run, localDate),
  );
}

async function dispatchRefresh(env, reason) {
  await githubRequest(env, workflowUrl(env, "/dispatches"), {
    method: "POST",
    body: JSON.stringify({
      ref: env.GITHUB_REF || "main",
      inputs: {
        refresh_mode: "normal",
      },
    }),
  });
  console.log(`Dispatched Quantiv daily refresh: ${reason}`);
}

async function dispatchAtTwo(env, localDate) {
  const runs = await normalRunsForDate(env, localDate);
  if (runs.length > 0) {
    console.log(
      `Daily refresh already exists for ${localDate}; earliest run=${Math.min(...runs.map((run) => Number(run.id)))}`,
    );
    return;
  }
  await dispatchRefresh(env, `Cloudflare 02:00 ET trigger for ${localDate}`);
}

export function watchdogRunState(run) {
  if (run.status === "queued") return "queued";
  if (run.status === "completed" && run.conclusion !== "success") return "failed";
  return "started";
}

async function watchdog(env, localDate) {
  const runs = await normalRunsForDate(env, localDate);
  if (runs.length === 0) {
    await dispatchRefresh(
      env,
      `02:10 ET watchdog found no workflow run for ${localDate}`,
    );
    return;
  }

  const earliest = [...runs].sort(
    (a, b) =>
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime() ||
      Number(a.id) - Number(b.id),
  )[0];
  const state = watchdogRunState(earliest);
  if (state === "queued") {
    console.warn(
      `Daily refresh run ${earliest.id} exists for ${localDate} but has not started by watchdog time; not duplicating it.`,
    );
  } else if (state === "failed") {
    console.error(
      `Daily refresh run ${earliest.id} already completed with ${earliest.conclusion}; automatic provider retry is intentionally disabled.`,
    );
  } else {
    console.log(
      `Daily refresh watchdog satisfied for ${localDate}: run=${earliest.id} status=${earliest.status}`,
    );
  }
}

export default {
  async scheduled(controller, env) {
    if (!env.GITHUB_TOKEN) {
      throw new Error("GITHUB_TOKEN secret is not configured");
    }
    const scheduledAt = new Date(controller.scheduledTime);
    const localDate = easternDate(scheduledAt);
    const mode = scheduledMode(scheduledAt);

    if (mode === "dispatch") {
      await dispatchAtTwo(env, localDate);
      return;
    }
    if (mode === "watchdog") {
      await watchdog(env, localDate);
      return;
    }
    console.log(
      `Ignoring UTC cron ${controller.cron}; it does not map to the active Eastern trigger hour.`,
    );
  },
};
