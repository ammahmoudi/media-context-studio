import { useState } from "react";

import { useQuery } from "@tanstack/react-query";

import {
	Activity,
	ArrowRight,
	Check,
	CircleAlert,
	Clock3,
	Cpu,
	HardDrive,
	Plus,
	Server,
} from "lucide-react";

import { Button } from "@/components/ui/button";

import { Input } from "@/components/ui/input";

import { duration, request } from "../api";

import type { Health, Job, Snapshot } from "../api";

import { Link, Pager, RunLink, Stage, Status } from "../components/common";

import { date, runPath } from "../navigation";

import type { Navigate } from "../navigation";

export function RunTable({
	records,

	navigate,
}: {
	records: Job[];

	navigate: Navigate;
}) {
	return (
		<div className="table-wrap">
			<table className="run-table">
				<thead>
					<tr>
						<th>VIDEO</th>
						<th>STATUS</th>
						<th>QUEUE / CREATED</th>
						<th>RUN TIME</th>
					</tr>
				</thead>
				<tbody>
					{records.map((r) => (
						<tr key={r.id}>
							<td>
								<RunLink job={r} navigate={navigate} />
							</td>
							<td>
								<Status status={r.status} />
							</td>
							<td>
								<span className="queue-chip">{r.queue ?? "default"}</span>
								<small>{date(r.created_at)}</small>
							</td>
							<td className="nowrap">{duration(r.elapsed_seconds)}</td>
						</tr>
					))}
				</tbody>
			</table>
			{!records.length && <div className="empty">No runs match this view.</div>}
		</div>
	);
}

export function Dashboard({
	snapshot: s,

	health,

	navigate,
}: {
	snapshot?: Snapshot;

	health?: Health;

	navigate: Navigate;
}) {
	const online = s?.workers.filter((w) => w.online).length ?? 0;

	return (
		<>
			<div className="intro">
				<div>
					<div className="eyebrow">OPERATIONS OVERVIEW</div>
					<h1>Your video pipeline, at a glance.</h1>
					<p>
						From recording to searchable context. All the moving parts, in one
						view.
					</p>
				</div>
				<Button onClick={() => navigate("/ui/new")}>
					<Plus size={16} />
					New run
				</Button>
			</div>
			<section className="metrics">
				{[
					{ label: "Processing", value: s?.counts.running, icon: Activity },

					{ label: "Waiting", value: s?.counts.queued, icon: Clock3 },

					{ label: "Ready", value: s?.counts.complete, icon: Check },

					{
						label: "Needs attention",

						value: s ? s.counts.failed + s.counts.partial : undefined,

						icon: CircleAlert,
					},
				].map(({ label, value, icon: Icon }) => (
					<div className="metric" key={label}>
						<span>
							{label}
							<Icon size={18} />
						</span>
						<strong>{value ?? "—"}</strong>
					</div>
				))}
			</section>
			<section className="infrastructure">
				<Link to="/ui/workers" navigate={navigate}>
					<span className="infra-icon">
						<Server size={19} />
					</span>
					<div>
						<strong>
							{online} worker{online === 1 ? "" : "s"} online
						</strong>
						<small>Independent inference slots</small>
					</div>
					<ArrowRight size={15} />
				</Link>
				<div>
					<span className="infra-icon">
						<HardDrive size={19} />
					</span>
					<div>
						<strong>
							{health?.database === "postgresql"
								? "PostgreSQL"
								: health?.database === "sqlite"
									? "SQLite"
									: "…"}{" "}
							+ {health?.storage === "s3" ? "S3 objects" : "local objects"}
						</strong>
						<small>Durable history & artifact storage</small>
					</div>
				</div>
				<div>
					<span className="infra-icon">
						<Cpu size={19} />
					</span>
					<div>
						<strong>
							{s ? `${s.capacity - s.available_slots} / ${s.capacity}` : "—"}{" "}
							queue slots
						</strong>
						<small>Includes uploads, running & waiting</small>
					</div>
				</div>
			</section>
			<div className="operations-grid">
				<section className="panel">
					<div className="panel-heading">
						<h2>
							<Activity size={17} />
							Processing now
						</h2>
						<span className="muted">Live · every 3s</span>
					</div>
					{!s?.running.length ? (
						<div className="empty">
							<span className="empty-icon">
								<Check size={21} />
							</span>
							<strong>No active processing</strong>
							<p>Your workers are ready for the next recording.</p>
						</div>
					) : (
						s.running.map((r) => (
							<div className="active-run" key={r.id}>
								<div className="row-between">
									<RunLink job={r} navigate={navigate} />
									<Status status={r.status} />
								</div>
								<Stage job={r} />
								<div className="row-between muted">
									<span>
										{r.queue} queue · attempt {r.attempt}
									</span>
									<span>Started {date(r.started_at)}</span>
								</div>
							</div>
						))
					)}
				</section>
				<section className="panel">
					<div className="panel-heading">
						<h2>
							<Clock3 size={17} />
							Up next
						</h2>
						<span className="muted">{s?.queued.length ?? 0} waiting</span>
					</div>
					{!s?.queued.length ? (
						<div className="empty">
							<strong>The queue is clear</strong>
							<p>Accepted videos wait here until a matching worker is free.</p>
						</div>
					) : (
						<div className="queue-list">
							{s.queued.map((r) => (
								<Link
									key={r.id}
									to={runPath(r.id)}
									navigate={navigate}
									className="queue-entry"
								>
									<span className="queue-number">{r.queue_position}</span>
									<span dir="auto">{r.source_file}</span>
									<span className="queue-chip">{r.queue}</span>
								</Link>
							))}
						</div>
					)}
					{!!s?.uploading.length && (
						<p className="muted pad">
							{s.uploading.length} input(s) being stored.
						</p>
					)}
				</section>
			</div>
			<section className="panel">
				<div className="panel-heading">
					<h2>Recent runs</h2>
					<Link to="/ui/runs" navigate={navigate} className="text-link">
						All runs <ArrowRight size={14} />
					</Link>
				</div>
				<RunTable records={s?.history ?? []} navigate={navigate} />
			</section>
		</>
	);
}

export function Runs({ navigate }: { navigate: Navigate }) {
	const [page, setPage] = useState(0),
		[search, setSearch] = useState(""),
		[draft, setDraft] = useState(""),
		[status, setStatus] = useState("");

	const query = useQuery({
		queryKey: ["history", page, search, status],

		queryFn: () =>
			request<Snapshot>(
				`/v1/jobs?limit=15&offset=${page * 15}&search=${encodeURIComponent(search)}${status ? `&status=${status}` : ""}`,
			),

		refetchInterval: 3000,
	});

	return (
		<>
			<div className="intro">
				<div>
					<div className="eyebrow">JOB LIBRARY</div>
					<h1>Every run has a record.</h1>
					<p>
						Browse saved results and investigate anything that needs attention.
					</p>
				</div>
				<Button onClick={() => navigate("/ui/new")}>
					<Plus size={16} />
					New run
				</Button>
			</div>
			<section className="panel">
				<form
					className="history-filters"
					onSubmit={(e) => {
						e.preventDefault();

						setSearch(draft);

						setPage(0);
					}}
				>
					<Input
						aria-label="Search runs"
						placeholder="Search videos or job IDs…"
						value={draft}
						onChange={(e) => setDraft(e.target.value)}
					/>
					<select
						aria-label="Filter status"
						value={status}
						onChange={(e) => {
							setStatus(e.target.value);

							setPage(0);
						}}
					>
						<option value="">All finished runs</option>
						{["complete", "partial", "failed", "cancelled"].map((x) => (
							<option key={x} value={x}>
								{x}
							</option>
						))}
					</select>
					<Button variant="outline">Search</Button>
				</form>
				{query.isError && <div className="error">{query.error.message}</div>}
				{query.isPending ? (
					<div className="empty">Loading history…</div>
				) : (
					<RunTable records={query.data?.history ?? []} navigate={navigate} />
				)}
				<Pager
					page={page}
					size={15}
					total={query.data?.total_history ?? 0}
					setPage={setPage}
				/>
			</section>
		</>
	);
}

export function Workers({
	snapshot: s,

	navigate,
}: {
	snapshot?: Snapshot;

	navigate: Navigate;
}) {
	return (
		<>
			<div className="intro">
				<div>
					<div className="eyebrow">RESOURCE MONITOR</div>
					<h1>The team behind the queue.</h1>
					<p>
						One job per worker. Heartbeats show availability, routing, and
						resource use.
					</p>
				</div>
			</div>
			<div className="worker-grid">
				{s?.workers.map((w) => (
					<section
						className={`panel worker-card ${w.online ? "" : "worker-offline"}`}
						key={w.id}
					>
						<div className="row-between">
							<span className="worker-device">
								<Cpu size={22} />
								{w.device === "cuda" ? "GPU worker" : "CPU worker"}
							</span>
							<span className={`connection ${w.online ? "" : "offline"}`}>
								<span className="live-dot" />
								{w.online ? "Online" : "Offline"}
							</span>
						</div>
						<h3>{w.hostname}</h3>
						<code>{w.id}</code>
						<div className="worker-stats">
							<div>
								<strong>{Math.round(w.cpu_percent)}%</strong>
								<small>Process CPU</small>
							</div>
							<div>
								<strong>{Math.round(w.memory_mb)} MB</strong>
								<small>Process RAM</small>
							</div>
							<div>
								<strong>{w.cpu_threads}</strong>
								<small>Threads</small>
							</div>
						</div>
						<p className="muted">Model: {w.model}</p>
						{w.scratch_free_mb != null && <p className="muted">Scratch disk: {(w.scratch_free_mb / 1024).toFixed(1)} GiB free</p>}
						{w.blocked_reason && <p className="error" role="status">Waiting for resources: {w.blocked_reason}</p>}
						{w.memory_available_mb != null && <p className="muted">
							{Math.round(w.memory_available_mb)} MiB available · {Math.round(w.memory_limit_mb ?? 0)} MiB {w.memory_scope} limit
						</p>}
						{w.peak_memory_mb != null && <p className="muted">
							Peak process tree: {Math.round(w.peak_memory_mb)} MiB · {w.max_parallel} active run per resource domain
						</p>}
						{w.gpu_memory_used_mb != null && (
							<p className="muted">
								GPU device: {Math.round(w.gpu_memory_used_mb)} /{" "}
								{w.gpu_memory_total_mb} MB VRAM · {w.gpu_utilization_percent}%
								utilization
							</p>
						)}
						<div className="worker-queues">
							{w.queues.map((q) => (
								<span className="queue-chip" key={q}>
									{q}
								</span>
							))}
						</div>
						<div className="worker-footer">
							{w.job_id ? (
								<Link
									navigate={navigate}
									to={runPath(w.job_id)}
									className="text-link"
								>
									Processing {w.job_id.slice(0, 8)} <ArrowRight size={14} />
								</Link>
							) : (
								<span>{w.stopping ? "Draining" : w.blocked_reason ? "Waiting for resources" : "Idle"}</span>
							)}
							<span>
								{Math.round(w.last_seen_seconds_ago)}s since heartbeat
							</span>
						</div>
					</section>
				))}
			</div>
			{!s?.workers.length && (
				<section className="panel empty">
					<strong>No workers have checked in</strong>
					<p>Jobs remain safely queued until a worker connects.</p>
				</section>
			)}
			<p className="muted operational-note">
				Online workers reported within the last 30 seconds. CPU can exceed 100%
				when several cores are in use. Queue names determine which worker can
				accept each run.
			</p>
		</>
	);
}
