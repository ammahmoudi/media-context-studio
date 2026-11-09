# Video Context dashboard

React/TypeScript UI for the local video extraction service. See [dashboard documentation](../docs/dashboard.md).

```powershell
pnpm install --frozen-lockfile
pnpm build
```

The build writes dashboard and offline Swagger assets into `../video_context/static/`. Start the Python service and open <http://127.0.0.1:18765/>.

For development, `pnpm dev` serves the UI under `/ui/` and proxies API requests to the running local service. `pnpm lint` checks the frontend.
