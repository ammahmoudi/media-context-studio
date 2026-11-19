import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	BookOpen,
	Clapperboard,
	LayoutDashboard,
	Layers3,
	ListVideo,
	LogOut,
	Plus,
	RefreshCw,
	Server,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, request } from "./api";
import type { Health, Snapshot } from "./api";
import { Link } from "./components/common";
import { Dashboard, Runs, Workers } from "./pages/Operations";
import NewRun from "./pages/NewRun";
import RunDetail from "./pages/RunDetail";
import "./App.css";

function currentPath() {
	const legacy = new URLSearchParams(location.search).get("job");
	return legacy ? `/ui/jobs/${encodeURIComponent(legacy)}` : location.pathname;
}
export default function App() {
	const [path, setPath] = useState(currentPath),
		client = useQueryClient();
	useEffect(() => {
		const listener = () => setPath(currentPath());
		window.addEventListener("popstate", listener);
		return () => window.removeEventListener("popstate", listener);
	}, []);
	function navigate(to: string) {
		history.pushState(null, "", to);
		setPath(to);
		window.scrollTo(0, 0);
	}
	const health = useQuery({
		queryKey: ["health"],
		queryFn: () => request<Health>("/health"),
		refetchInterval: 10000,
	});
	const snapshot = useQuery({
		queryKey: ["snapshot"],
		queryFn: () => request<Snapshot>("/v1/jobs?limit=5"),
		refetchInterval: (query) =>
			query.state.error instanceof ApiError && query.state.error.status === 401
				? false
				: 3000,
		retry: (count, error) =>
			!(error instanceof ApiError && error.status === 401) && count < 1,
	});
	const login = useMutation({
		mutationFn: (api_key: string) =>
			request("/session", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ api_key }),
			}),
		onSuccess: () => client.invalidateQueries(),
	});
	const logout = useMutation({
		mutationFn: () => request("/session", { method: "DELETE" }),
		onSuccess: () => {
			client.clear();
			location.assign("/ui/");
		},
	});
	const needsLogin =
		snapshot.error instanceof ApiError && snapshot.error.status === 401;
	const detail = path.match(/^\/ui\/jobs\/([^/]+)\/?$/)?.[1];
	const page = detail
		? "detail"
		: path.startsWith("/ui/new")
			? "new"
			: path.startsWith("/ui/runs")
				? "runs"
				: path.startsWith("/ui/workers")
					? "workers"
					: "dashboard";
	const nav = [
		{ id: "dashboard", to: "/ui/", title: "Overview", icon: LayoutDashboard },
		{ id: "runs", to: "/ui/runs", title: "All runs", icon: ListVideo },
		{ id: "workers", to: "/ui/workers", title: "Workers", icon: Server },
	];
	return (
		<div className="studio-layout">
			<aside className="sidebar">
				<Link to="/ui/" navigate={navigate} className="brand">
					<span className="brand-mark">
						<Layers3 size={22} />
					</span>
					<div>
						Media Context<small>EXTRACTION STUDIO</small>
					</div>
				</Link>
				<div className="sidebar-label">WORKSPACE</div>
				<nav className="side-nav">
					{nav.map(({ id, to, title, icon: Icon }) => (
						<Link
							to={to}
							navigate={navigate}
							key={id}
							className={
								page === id || (id === "runs" && page === "detail")
									? "current"
									: ""
							}
						>
							<Icon size={18} />
							{title}
							{id === "workers" && (
								<span>
									{snapshot.data?.workers.filter((w) => w.online).length ?? 0}
								</span>
							)}
						</Link>
					))}
					<Link
						to="/ui/new"
						navigate={navigate}
						className={`new-run-nav ${page === "new" ? "current" : ""}`}
					>
						<Plus size={18} />
						New run
					</Link>
				</nav>
				<div className="sidebar-bottom">
					<a href="/docs" target="_blank" rel="noreferrer">
						<BookOpen size={17} />
						Swagger API ↗
					</a>
					<div className="storage-label">
						<span className="live-dot" />
						{health.data?.database ?? "Connecting"} ·{" "}
						{health.data?.storage === "s3" ? "S3 objects" : "local storage"}
					</div>
				</div>
			</aside>
			<div className="studio-main">
				<header className="studio-topbar">
					<div>
						<Clapperboard size={16} />
						<span>
							{
								{
									dashboard: "Overview",
									runs: "Run library",
									workers: "Workers & resources",
									new: "Create a run",
									detail: "Run workspace",
								}[page]
							}
						</span>
					</div>
					<div>
						<span className={`connection ${health.isError ? "offline" : ""}`}>
							<span className="live-dot" />
							{health.isError
								? "Disconnected"
								: health.data
									? "Service online"
									: "Connecting…"}
						</span>
						<Button
							aria-label="Refresh data"
							variant="ghost"
							size="icon-sm"
							onClick={() => client.invalidateQueries()}
						>
							<RefreshCw size={15} />
						</Button>
						{health.data?.auth_required && (
							<Button
								aria-label="Sign out"
								variant="ghost"
								size="icon-sm"
								onClick={() => logout.mutate()}
							>
								<LogOut size={16} />
							</Button>
						)}
					</div>
				</header>
				<main className="studio-content">
					{needsLogin ? (
						<section className="panel login-panel">
							<div className="eyebrow">PRIVATE WORKSPACE</div>
							<h1>Welcome to your media studio.</h1>
							<p className="muted">Sign in with this service’s API key.</p>
							<form
								onSubmit={(e) => {
									e.preventDefault();
									login.mutate(
										String(new FormData(e.currentTarget).get("api_key")),
									);
								}}
							>
								<label>
									API key
									<Input
										name="api_key"
										type="password"
										autoComplete="current-password"
										required
									/>
								</label>
								{login.isError && (
									<p className="error" role="alert">
										{login.error.message}
									</p>
								)}
								<Button type="submit" disabled={login.isPending}>
									{login.isPending ? "Signing in…" : "Sign in"}
								</Button>
							</form>
						</section>
					) : (
						<>
							{(health.isError || snapshot.isError) && (
								<div className="error" role="alert">
									Unable to refresh service data:{" "}
									{snapshot.error?.message ?? health.error?.message}
								</div>
							)}
							{page === "dashboard" && (
								<Dashboard
									snapshot={snapshot.data}
									health={health.data}
									navigate={navigate}
								/>
							)}
							{page === "runs" && <Runs navigate={navigate} />}
							{page === "workers" && (
								<Workers snapshot={snapshot.data} navigate={navigate} />
							)}
							{page === "new" && health.data && (
								<NewRun
									health={health.data}
									snapshot={snapshot.data}
									navigate={navigate}
								/>
							)}
							{page === "detail" && detail && (
								<RunDetail key={detail} id={detail} navigate={navigate} />
							)}
						</>
					)}
					<footer>
						<span>
							Media Context ·{" "}
							{health.data?.worker_mode === "external"
								? "Distributed workers"
								: "Laptop mode"}
						</span>
						<span>Private video → structured evidence</span>
					</footer>
				</main>
			</div>
		</div>
	);
}
