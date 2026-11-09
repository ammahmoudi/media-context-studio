import { useState } from "react";
import type { FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	Activity,
	ArrowRight,
	BookOpen,
	Check,
	ChevronLeft,
	ChevronRight,
	Clock3,
	FileVideo,
	Layers3,
	LoaderCircle,
	Plus,
	RefreshCw,
	Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { artifact, duration, request } from "./api";
import type { Context, Health, Job, Snapshot } from "./api";
import "./App.css";

const active = (status?: string) => status === "running" || status === "queued";
const date = (value?: string) =>
	value
		? new Date(value).toLocaleString([], {
				month: "short",
				day: "numeric",
				hour: "2-digit",
				minute: "2-digit",
			})
		: "—";
function Status({ status }: { status: string }) {
	return (
		<Badge variant="outline" className={`status ${status}`}>
			{status === "running" && <span className="live-dot" />}
			{status}
		</Badge>
	);
}
function Stage({ job }: { job: Job }) {
	const match = job.stage.match(/^(ocr|speech) ([\d.]+)\/([\d.]+)/);
	const value = match
		? Math.min(100, (Number(match[2]) / Number(match[3])) * 100)
		: null;
	return (
		<div className="stage">
			<span>{job.stage}</span>
			{value != null && (
				<>
					<progress
						aria-label={`${match![1]} stage progress`}
						max={100}
						value={value}
					/>
					<small>{Math.round(value)}% of this stage</small>
				</>
			)}
		</div>
	);
}

export default function App() {
	const client = useQueryClient();
	const [offset, setOffset] = useState(0);
	const [selected, setSelected] = useState<string | null>(
		new URLSearchParams(location.search).get("job"),
	);
	const [mode, setMode] = useState("path");
	const [notice, setNotice] = useState("");
	const [tab, setTab] = useState("Transcript");
	const [showAll, setShowAll] = useState(false);
	const [windowResult, setWindowResult] = useState<Context | null>(null);
	const health = useQuery({
		queryKey: ["health"],
		queryFn: () => request<Health>("/health"),
		refetchInterval: 10000,
	});
	const jobs = useQuery({
		queryKey: ["jobs", offset],
		queryFn: () => request<Snapshot>(`/v1/jobs?limit=10&offset=${offset}`),
		refetchInterval: 3000,
	});
	const job = useQuery({
		queryKey: ["job", selected],
		queryFn: () => request<Job>(`/v1/jobs/${selected}`),
		enabled: !!selected,
		refetchInterval: (q) => (active(q.state.data?.status) ? 3000 : false),
	});
	const result = useQuery({
		queryKey: ["result", selected],
		queryFn: () => request<Context>(`/v1/jobs/${selected}/result`),
		enabled: !!job.data && !active(job.data.status),
		retry: false,
	});
	const interval = useMutation({
		mutationFn: ({
			jobId,
			start,
			end,
		}: {
			jobId: string;
			start: string;
			end: string;
		}) =>
			request<Context>(
				`/v1/jobs/${jobId}/context?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
			),
		onSuccess: (data, variables) => {
			if (variables.jobId === selected) {
				setWindowResult(data);
				setShowAll(false);
			}
		},
	});
	function choose(id: string) {
		setSelected(id);
		setWindowResult(null);
		setShowAll(false);
		interval.reset();
		history.replaceState(null, "", `?job=${encodeURIComponent(id)}`);
	}
	const submit = useMutation({
		mutationFn: async (form: HTMLFormElement) => {
			const fields = new FormData(form);
			const options = {
				speech: fields.has("speech"),
				ocr: fields.has("ocr"),
				ocr_deduplicate: fields.has("deduplicate"),
				language: fields.get("language") || null,
				ocr_language: fields.get("ocr_language"),
				sample_interval_seconds: Number(fields.get("interval")),
				max_frames: Number(fields.get("max_frames")),
			};
			if (mode === "upload") {
				const body = new FormData();
				body.append("file", fields.get("file") as File);
				body.append("options_json", JSON.stringify(options));
				return request<Job>("/v1/jobs/upload", { method: "POST", body });
			}
			return request<Job>("/v1/jobs", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					path: String(fields.get("path")).trim(),
					options,
				}),
			});
		},
		onSuccess: (data) => {
			choose(data.id);
			setNotice("Video added. Watch its progress below.");
			client.invalidateQueries({ queryKey: ["jobs"] });
		},
	});
	const snapshot = jobs.data;
	const output = windowResult ?? result.data;
	function onSubmit(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		setNotice("");
		submit.mutate(event.currentTarget);
	}
	const refresh = () => {
		client.invalidateQueries();
	};
	return (
		<div className="app-shell">
			<header className="topbar">
				<a className="brand" href="/ui/">
					<span className="brand-mark">
						<Layers3 size={21} />
					</span>
					Video Context<span className="local-label">LOCAL STUDIO</span>
				</a>
				<nav>
					<a href="/docs" target="_blank" rel="noreferrer">
						<BookOpen size={16} /> Swagger API <ArrowRight size={14} />
					</a>
					<span className={`connection ${health.isError ? "offline" : ""}`}>
						<span className="live-dot" />
						{health.isError
							? "Disconnected"
							: health.data
								? "Service online"
								: "Connecting…"}
					</span>
				</nav>
			</header>
			<main>
				<section className="intro">
					<div>
						<div className="eyebrow">VIDEO → STRUCTURED CONTEXT</div>
						<h1>A clearer view of every video.</h1>
						<p>
							Extract speech, screen text, and frames. Follow every run in one
							place.
						</p>
					</div>
					<Button variant="outline" onClick={refresh}>
						<RefreshCw size={15} /> Refresh
					</Button>
				</section>
				{(jobs.isError || health.isError) && (
					<div className="error" role="alert">
						Cannot reach the local service.{" "}
						{jobs.error?.message ?? health.error?.message}{" "}
						<button onClick={refresh}>Retry</button>
					</div>
				)}
				<section className="metrics" aria-label="Service overview">
					{[
						{
							label: "Running now",
							value: snapshot?.counts.running,
							icon: Activity,
						},
						{
							label: "In the queue",
							value: snapshot?.counts.queued,
							icon: Clock3,
						},
						{
							label: "Completed",
							value: snapshot?.counts.complete,
							icon: Check,
						},
					].map(({ label, value, icon: Icon }) => (
						<div className="metric" key={label}>
							<span>
								{label}
								<Icon size={17} />
							</span>
							<strong>{value ?? "—"}</strong>
						</div>
					))}
					<div className="metric model">
						<span>
							Speech engine
							<FileVideo size={17} />
						</span>
						<strong>{health.data?.speech.model ?? "—"}</strong>
						<small>{health.data?.speech.device ?? "…"} · local inference</small>
					</div>
				</section>
				<div className="workspace">
					<div className="runs-column">
						<section className="panel">
							<div className="panel-heading">
								<h2>
									<Activity size={18} /> Live runs
								</h2>
								<span className="muted">Updates every 3s</span>
							</div>
							{jobs.isPending && <p className="empty">Loading runs…</p>}
							{snapshot &&
								!snapshot.running.length &&
								!snapshot.queued.length && (
									<div className="empty">
										<span className="empty-icon">
											<Check size={22} />
										</span>
										<strong>All caught up</strong>
										<p>New videos will appear here as they run.</p>
									</div>
								)}
							{snapshot?.running.map((r) => (
								<div className="active-run" key={r.id}>
									<div className="row-between">
										<strong dir="auto">{r.source_file}</strong>
										<Status status={r.status} />
									</div>
									<Stage job={r} />
									<div className="row-between muted">
										<span>Started {date(r.started_at ?? r.created_at)}</span>
										<Button
											size="sm"
											variant="ghost"
											onClick={() => choose(r.id)}
										>
											Inspect <ArrowRight size={14} />
										</Button>
									</div>
								</div>
							))}
							{!!snapshot?.queued.length && (
								<div className="queue-list">
									<div className="eyebrow">UP NEXT · FIRST IN, FIRST OUT</div>
									{snapshot.queued.map((r) => (
										<button key={r.id} onClick={() => choose(r.id)}>
											<span className="queue-number">{r.queue_position}</span>
											<span dir="auto">{r.source_file}</span>
											<Status status="queued" />
										</button>
									))}
								</div>
							)}
						</section>
						<section className="panel history">
							<div className="panel-heading">
								<h2>
									<Clock3 size={18} /> Run history
								</h2>
								<span className="muted">
									{snapshot?.total_history ?? "—"} runs
								</span>
							</div>
							<div className="table-wrap">
								<table>
									<thead>
										<tr>
											<th>Video / submitted</th>
											<th>Status</th>
											<th>Time</th>
											<th>
												<span className="sr-only">Action</span>
											</th>
										</tr>
									</thead>
									<tbody>
										{snapshot?.history.map((r) => (
											<tr
												key={r.id}
												className={selected === r.id ? "selected" : ""}
											>
												<td>
													<span
														className="file-name"
														dir="auto"
														title={r.source_file}
													>
														{r.source_file}
													</span>
													<small>
														{date(r.created_at)} · {r.id.slice(0, 8)}
													</small>
												</td>
												<td>
													<Status status={r.status} />
												</td>
												<td className="nowrap">
													{duration(r.elapsed_seconds)}
												</td>
												<td>
													<Button
														size="sm"
														variant="ghost"
														aria-label={`Inspect job ${r.id}`}
														onClick={() => choose(r.id)}
													>
														<ArrowRight size={16} />
													</Button>
												</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
							{snapshot && !snapshot.history.length && (
								<p className="empty">No finished runs yet.</p>
							)}
							<div className="pagination">
								<span>
									{snapshot?.total_history
										? `${offset + 1}–${Math.min(offset + 10, snapshot.total_history)} of ${snapshot.total_history}`
										: "0 runs"}
								</span>
								<div>
									<Button
										size="icon-sm"
										variant="outline"
										aria-label="Previous page"
										disabled={!offset}
										onClick={() => setOffset(Math.max(0, offset - 10))}
									>
										<ChevronLeft size={16} />
									</Button>
									<Button
										size="icon-sm"
										variant="outline"
										aria-label="Next page"
										disabled={
											!snapshot || offset + 10 >= snapshot.total_history
										}
										onClick={() => setOffset(offset + 10)}
									>
										<ChevronRight size={16} />
									</Button>
								</div>
							</div>
						</section>
					</div>
					<section className="panel new-run">
						<div className="panel-heading">
							<h2>
								<Plus size={18} /> New run
							</h2>
							<span className="muted">
								{snapshot?.available_slots ?? "—"} slots free
							</span>
						</div>
						<form onSubmit={onSubmit}>
							<div className="mode-switch">
								{["path", "upload"].map((m) => (
									<button
										key={m}
										type="button"
										className={mode === m ? "chosen" : ""}
										onClick={() => setMode(m)}
									>
										{m === "path" ? "Local path" : "Upload video"}
									</button>
								))}
							</div>
							{mode === "path" ? (
								<label>
									Video file path
									<Input
										name="path"
										required
										placeholder="C:\Videos\lecture.mp4"
									/>
									<small>Use the full path to a video on this laptop.</small>
								</label>
							) : (
								<label className="upload-box">
									<Upload size={23} />
									<span>Choose a video</span>
									<Input
										name="file"
										type="file"
										accept="video/*,.mkv"
										required
									/>
									<small>Saved locally · up to 8 GiB</small>
								</label>
							)}
							<div className="form-grid">
								<label>
									Spoken language
									<select name="language" defaultValue="fa">
										<option value="">Auto-detect</option>
										<option value="fa">Persian</option>
										<option value="en">English</option>
										<option value="ar">Arabic</option>
									</select>
								</label>
								<label>
									Screen text
									<select name="ocr_language" defaultValue="en">
										{(health.data?.prepared_ocr_profiles.length
											? health.data.prepared_ocr_profiles
											: ["en"]
										).map((p) => (
											<option key={p} value={p}>
												{(
													{
														en: "English",
														fa: "Persian",
														ar: "Arabic",
													} as Record<string, string>
												)[p] ?? p}
											</option>
										))}
									</select>
								</label>
							</div>
							<div className="form-grid">
								<label>
									Sample every (sec)
									<Input
										name="interval"
										type="number"
										min={1}
										max={600}
										defaultValue={15}
										required
									/>
								</label>
								<label>
									Maximum frames
									<Input
										name="max_frames"
										type="number"
										min={1}
										max={2000}
										defaultValue={500}
										required
									/>
								</label>
							</div>
							<div className="checks">
								<label>
									<input type="checkbox" name="speech" defaultChecked /> Speech
									captions
								</label>
								<label>
									<input type="checkbox" name="ocr" defaultChecked /> Screen
									text (OCR)
								</label>
								<label>
									<input type="checkbox" name="deduplicate" defaultChecked />{" "}
									Reuse unchanged text
								</label>
							</div>
							{submit.isError && (
								<div className="error" role="alert">
									{submit.error.message}
								</div>
							)}
							{notice && (
								<p className="success" role="status">
									{notice}
								</p>
							)}
							<Button
								className="submit-button"
								type="submit"
								disabled={submit.isPending || snapshot?.available_slots === 0}
							>
								{submit.isPending ? (
									<LoaderCircle className="spin" size={17} />
								) : (
									<Plus size={17} />
								)}{" "}
								{submit.isPending ? "Submitting…" : "Add to queue"}
							</Button>
							<p className="form-note">
								One video runs at a time. Everything stays on this laptop.
							</p>
						</form>
					</section>
				</div>
				<section className="panel inspector" aria-label="Job inspector">
					<div className="panel-heading">
						<h2>
							<FileVideo size={18} /> Context explorer
						</h2>
						{job.data && <Status status={job.data.status} />}
					</div>
					{!selected ? (
						<div className="empty">
							<strong>Open a run to explore its context</strong>
							<p>Read captions, inspect frames, or query a time range.</p>
						</div>
					) : (
						<div className="inspector-body">
							<div className="inspector-title">
								<div>
									<h3 dir="auto">{job.data?.source_file ?? "Loading job…"}</h3>
									<code>{selected}</code>
								</div>
								<div className="artifact-links">
									<a
										href={`/v1/jobs/${selected}`}
										target="_blank"
										rel="noreferrer"
									>
										Job JSON ↗
									</a>
									{result.data && (
										<>
											<a
												href={`/v1/jobs/${selected}/result`}
												target="_blank"
												rel="noreferrer"
											>
												Context JSON ↗
											</a>
											<a
												href={artifact(selected, "context.md")}
												target="_blank"
												rel="noreferrer"
											>
												Report ↗
											</a>
											<a
												href={artifact(selected, "captions/speech.srt")}
												download
											>
												Captions ↓
											</a>
										</>
									)}
								</div>
							</div>
							{(job.isError || job.data?.error) && (
								<div className="error" role="alert">
									{job.error?.message ?? job.data?.error}
								</div>
							)}
							{active(job.data?.status) && (
								<div className="inspector-progress">
									<Stage job={job.data!} />
									<p className="muted">
										Results will appear here when processing finishes.
									</p>
								</div>
							)}
							{result.isFetching && (
								<p className="muted">Loading extracted context…</p>
							)}
							{result.isError && (
								<div className="error" role="alert">
									{result.error.message}
								</div>
							)}
							{result.data && (
								<>
									{!!result.data.warnings?.length && (
										<div className="error">
											{result.data.warnings.join("\n")}
										</div>
									)}
									<div className="stage-summary">
										{Object.entries(result.data.stages ?? {}).map(
											([name, s]) => (
												<div key={name}>
													<span>{name}</span>
													<strong>{s.status}</strong>
													<small>
														{s.elapsed_seconds != null
															? duration(s.elapsed_seconds)
															: "—"}
													</small>
													{s.error && <p>{s.error}</p>}
												</div>
											),
										)}
									</div>
									<form
										className="range-form"
										onSubmit={(e) => {
											e.preventDefault();
											const f = new FormData(e.currentTarget);
											interval.mutate({
												jobId: selected,
												start: String(f.get("start")),
												end: String(f.get("end")),
											});
										}}
									>
										<span>Explore a time range</span>
										<label>
											<span className="sr-only">Start seconds</span>
											<Input
												name="start"
												aria-label="Start seconds"
												type="number"
												min={0}
												step="any"
												defaultValue={0}
												required
											/>
										</label>
										<span>to</span>
										<label>
											<span className="sr-only">End seconds</span>
											<Input
												name="end"
												aria-label="End seconds"
												type="number"
												min={0.001}
												step="any"
												defaultValue={Math.min(
													60,
													result.data.source?.duration_seconds ?? 60,
												)}
												required
											/>
										</label>
										<span>seconds</span>
										<Button variant="outline" disabled={interval.isPending}>
											Query
										</Button>
										{windowResult && (
											<Button
												type="button"
												variant="ghost"
												onClick={() => {
													setWindowResult(null);
													setShowAll(false);
													interval.reset();
												}}
											>
												Show full video
											</Button>
										)}
									</form>
									{interval.isError && (
										<p className="error" role="alert">
											{interval.error.message}
										</p>
									)}
									<div
										className="result-tabs"
										role="tablist"
										aria-label="Context views"
									>
										{["Transcript", "Screen text", "Frames", "JSON"].map(
											(t) => (
												<button
													role="tab"
													aria-selected={tab === t}
													key={t}
													onClick={() => {
														setTab(t);
														setShowAll(false);
													}}
												>
													{t}
												</button>
											),
										)}
										<span>
											{windowResult ? "Time range" : "Full video"} ·{" "}
											{output?.speech_segments.length ?? 0} segments ·{" "}
											{output?.frames.length ?? 0} frames
										</span>
									</div>
									{tab === "Transcript" && (
										<div className="transcript">
											{!output?.speech_segments.length && (
												<p className="muted">
													No speech segments in this context.
												</p>
											)}
											{(showAll
												? output?.speech_segments
												: output?.speech_segments.slice(0, 60)
											)?.map((s) => (
												<div key={s.id}>
													<time>{duration(s.start_seconds)}</time>
													<p dir="auto">{s.text}</p>
												</div>
											))}
											{!showAll &&
												(output?.speech_segments.length ?? 0) > 60 && (
													<Button
														variant="outline"
														onClick={() => setShowAll(true)}
													>
														Show all {output?.speech_segments.length} segments
													</Button>
												)}
										</div>
									)}
									{tab === "Screen text" && (
										<div className="text-evidence">
											{!output?.on_screen_text.length && (
												<p className="muted">No screen text in this context.</p>
											)}
											{(showAll
												? output?.on_screen_text
												: output?.on_screen_text.slice(0, 100)
											)?.map((t) => (
												<p key={t.id} dir="auto">
													{t.text}
													<small>
														{t.frame_ids?.length ?? 1} frame observations
													</small>
												</p>
											))}
											{!showAll &&
												(output?.on_screen_text.length ?? 0) > 100 && (
													<Button
														variant="outline"
														onClick={() => setShowAll(true)}
													>
														Show all text observations
													</Button>
												)}
										</div>
									)}
									{tab === "Frames" && (
										<div className="frames-grid">
											{!output?.frames.length && (
												<p className="muted">No frames in this context.</p>
											)}
											{output?.frames.map((f) => (
												<a
													href={artifact(selected, f.path)}
													key={f.id}
													target="_blank"
													rel="noreferrer"
												>
													<img
														src={artifact(selected, f.path)}
														alt={`Video frame at ${duration(f.timestamp_seconds)}`}
														loading="lazy"
													/>
													<span>
														{duration(f.timestamp_seconds)}
														<ArrowRight size={13} />
													</span>
												</a>
											))}
										</div>
									)}
									{tab === "JSON" && (
										<pre className="json-preview">
											{JSON.stringify(output, null, 2)}
										</pre>
									)}
								</>
							)}
						</div>
					)}
				</section>
				<footer>
					<span>Video Context / local extraction service</span>
					<span>
						Need another endpoint?{" "}
						<a href="/docs" target="_blank" rel="noreferrer">
							Open Swagger →
						</a>
					</span>
				</footer>
			</main>
		</div>
	);
}
