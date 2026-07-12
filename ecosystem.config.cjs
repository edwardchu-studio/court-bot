module.exports = {
  apps: [{
    name: "court-bot-ui",
    cwd: "/Users/edwardchu/projects/court-bot",
    script: ".venv/bin/uvicorn",
    args: "webui.server:app --host 0.0.0.0 --port 7792",
    interpreter: "none",
    autorestart: true,
    max_restarts: 10,
    env: { TZ: "America/New_York" },
    out_file: "/Users/edwardchu/projects/court-bot/logs/pm2-ui.out.log",
    error_file: "/Users/edwardchu/projects/court-bot/logs/pm2-ui.err.log",
  }],
};
