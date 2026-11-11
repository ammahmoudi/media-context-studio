import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	ArrowLeft,
	Download,
	FileJson,
	Film,
	RotateCcw,
	Square,
	Play,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { artifact, duration, request } from "../api";
import type { Context, Job } from "../api";
import { Link, Pager, Stage, Status } from "../components/common";
import { active, date, runPath } from "../navigation";
import type { Navigate } from "../navigation";
import RunExport from "../components/RunExport";

const bytes = (n?: number) =>
	n == null
		? "—"
		: n > 1024 ** 3
			? `${(n / 1024 ** 3).toFixed(2)} GB`
			: `${(n / 1024 ** 2).toFixed(1)} MB`;
export default function RunDetail({
	id,
	navigate,
}: {
	id: string;
	navigate: Navigate;
}) {
	const client = useQueryClient(),
		player = useRef<HTMLVideoElement>(null);
	const [tab, setTab] = useState("Overview"),
		[page, setPage] = useState(0),
		[search, setSearch] = useState(""),
		[framePage, setFramePage] = useState(0);
	const [windowResult, setWindowResult] = useState<Context | null>(null);
	const [videoError, setVideoError] = useState(false);
	const [exportOpen, setExportOpen] = useState(false);
	const job = useQuery({
		queryKey: ["job", id],
		queryFn: () => request<Job>(`/v1/jobs/${id}`),
		refetchInterval: (q) => (active(q.state.data?.status) ? 3000 : false),
	});
	const result = useQuery({
		queryKey: ["result", id],
		queryFn: () => request<Context>(`/v1/jobs/${id}/result`),
		enabled: !!job.data && !active(job.data.status),
		retry: false,
		gcTime: 60000,
	});
	const action = useMutation({
		mutationFn: (name: "cancel" | "retry") =>
			request<Job>(`/v1/jobs/${id}/${name}`, { method: "POST" }),
		onSuccess: (r) => {
			client.invalidateQueries();
			if (r.id !== id) navigate(runPath(r.id));
		},
	});
	const range = useMutation({
		mutationFn: ({ start, end }: { start: string; end: string }) =>
			request<Context>(
				`/v1/jobs/${id}/context?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
			),
		onSuccess: (r) => {
			setWindowResult(r);
			setPage(0);
			setFramePage(0);
		},
	});
	function seek(seconds: number) {
		if (player.current) {
			player.current.currentTime = seconds;
			player.current.scrollIntoView({ behavior: "smooth", block: "center" });
		}
	}
	const r = job.data,
		context = windowResult ?? result.data;
	const transcript = (context?.speech_segments ?? []).filter((s) =>
		s.text.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
	);
	const viewPage = Math.min(
		page,
		Math.max(0, Math.ceil(transcript.length / 80) - 1),
	);
	if (job.isPending)
		return <section className="panel empty">Loading run…</section>;
	if (job.isError)
		return (
			<div role="alert" className="error">
				{job.error.message}
			</div>
		);
	return (
		<>
			<Link to="/ui/runs" navigate={navigate} className="back-link">
				<ArrowLeft size={14} />
				All runs
			</Link>
			<div className="detail-heading">
				<div>
					<div className="eyebrow">RUN {id.slice(0, 8)}</div>
					<h1 dir="auto">{r?.source_file}</h1>
					<div className="detail-subtitle">
						<Status status={r!.status} />
						<span>{date(r?.created_at)}</span>
						<span>{r?.queue ?? "default"} queue</span>
						<span>{bytes(r?.input_bytes)}</span>
					</div>
				</div>
				<div className="detail-actions">
					{(r?.video_available || !!r?.artifacts?.length) && <Button variant="outline" onClick={() => setExportOpen(!exportOpen)} aria-expanded={exportOpen}>
						<Download size={14} /> Download files
					</Button>}
					{["queued", "running"].includes(r!.status) && (
						<Button
							variant="outline"
							disabled={action.isPending}
							onClick={() => {
								if (
									confirm(
										"Cancel this run? Processing will stop at its next checkpoint.",
									)
								)
									action.mutate("cancel");
							}}
						>
							<Square size={14} />
							Cancel run
						</Button>
					)}
					{["failed", "partial", "cancelled"].includes(r!.status) &&
						r?.video_available && (
							<Button
								variant="outline"
								disabled={action.isPending}
								onClick={() => action.mutate("retry")}
							>
								<RotateCcw size={14} />
								Retry as new run
							</Button>
						)}
					{result.data && (
						<Button asChild variant="outline">
							<a href={artifact(id, "captions/speech.srt")} download>
								<Download size={14} />
								SRT captions
							</a>
						</Button>
					)}
				</div>
			</div>
			{exportOpen && <RunExport key={id} id={id} />}
			{(r?.error || action.isError) && (
				<div role="alert" className="error">
					{action.error?.message ?? r?.error}
				</div>
			)}
			{active(r?.status) && (
				<section className="panel processing-banner">
					<div className="row-between">
						<strong>Processing updates live</strong>
						<span className="muted">
							Attempt {r?.attempt ?? 0} ·{" "}
							{r?.worker_id?.split("-").slice(0, -1).join("-") ??
								"Waiting for a worker"}
						</span>
					</div>
					<Stage job={r!} />
				</section>
			)}
			<div className="detail-workspace">
				<section className="video-panel">
					<div className="video-title">
						<Film size={16} />
						Source video
						<span>
							{duration(
								result.data?.source?.duration_seconds ??
									r?.source?.duration_seconds,
							)}
						</span>
					</div>
					{r?.video_available ? (
						<video
							key={id}
							ref={player}
							controls
							preload="metadata"
							onError={() => setVideoError(true)}
						>
							<source src={`/v1/jobs/${id}/video`} />
							{!!result.data?.speech_segments.length && (
								<track
									kind="captions"
									src={artifact(id, "captions/speech.vtt")}
									srcLang={result.data.speech_segments[0]?.language ?? "fa"}
									label="Speech captions"
									default
								/>
							)}
							Your browser does not support video playback.
						</video>
					) : (
						<div className="video-unavailable">
							<Film size={30} />
							<strong>Source video is unavailable</strong>
							<p>
								Import the original video or create a new run to enable
								playback.
							</p>
						</div>
					)}
					<div className="video-note">
						{videoError && (
							<p>
								Browser playback failed.{" "}
								<a href={`/v1/jobs/${id}/video`} download>
									Download the source video
								</a>{" "}
								to play it locally.
							</p>
						)}
						{result.data?.speech_segments.length
							? "Captions are generated from the saved speech transcript. Toggle them with the player’s captions control."
							: "Captions appear after speech extraction completes."}
					</div>
				</section>
				<aside className="panel run-facts">
					<h2>Run details</h2>
					<dl>
						<div>
							<dt>Run time</dt>
							<dd>{duration(r?.elapsed_seconds)}</dd>
						</div>
						{r?.resources && <div>
							<dt>Peak RAM (sampled)</dt>
							<dd>{Math.round(r.resources.peak_memory_mb)} MiB</dd>
						</div>}
						<div>
							<dt>Worker</dt>
							<dd>{r?.worker_id ?? "Not assigned"}</dd>
						</div>
						<div>
							<dt>Attempts</dt>
							<dd>{r?.attempt ?? "Legacy"}</dd>
						</div>
						<div>
							<dt>Finished</dt>
							<dd>{date(r?.finished_at)}</dd>
						</div>
						<div>
							<dt>Job ID</dt>
							<dd>
								<code>{id}</code>
							</dd>
						</div>
					</dl>
					<a
						className="text-link"
						href={`/v1/jobs/${id}`}
						target="_blank"
						rel="noreferrer"
					>
						Job metadata <FileJson size={14} />
					</a>
				</aside>
			</div>
			<section className="panel detail-context">
				<div
					className="result-tabs detail-tabs"
					role="tablist"
					aria-label="Run views"
				>
					{["Overview", "Transcript", "Screen text", "Frames", "Files"].map(
						(t) => (
							<button
								role="tab"
								aria-selected={tab === t}
								onClick={() => setTab(t)}
								key={t}
							>
								{t}
							</button>
						),
					)}
				</div>
				{result.isFetching ? (
					<div className="empty">Loading extracted context…</div>
				) : !result.data ? (
					<div className="empty">
						<strong>
							{active(r?.status)
								? "Extraction is in progress"
								: "No published context"}
						</strong>
						<p>
							{active(r?.status)
								? "Results will be available when the worker finishes and saves its artifacts."
								: (result.error?.message ??
									"This run has no saved extraction result.")}
						</p>
					</div>
				) : (
					<div className="detail-content">
						{!!result.data.warnings?.length && (
							<div className="error">{result.data.warnings.join("\n")}</div>
						)}
						{tab === "Overview" && (
							<>
								<div className="extraction-counts">
									<div>
										<strong>{result.data.frames.length}</strong>
										<span>sampled frames</span>
									</div>
									<div>
										<strong>{result.data.speech_segments.length}</strong>
										<span>speech segments</span>
									</div>
									<div>
										<strong>{result.data.on_screen_text.length}</strong>
										<span>text observations</span>
									</div>
								</div>
								<h3>Extraction stages</h3>
								<div className="stage-timeline">
									{Object.entries(result.data.stages ?? {}).map(([name, s]) => (
										<div key={name}>
											<span className={`timeline-dot ${s.status}`} />
											<strong>{name}</strong>
											<Status status={s.status} />
											<time>{duration(s.elapsed_seconds)}</time>
											{s.error && <p className="error">{s.error}</p>}
										</div>
									))}
								</div>
								<p className="muted">
									Recognized text and speech are extracted evidence. Names,
									formulas, and technical vocabulary may need correction.
								</p>
							</>
						)}
						{["Transcript", "Screen text", "Frames"].includes(tab) && (
							<form
								className="range-form"
								onSubmit={(e) => {
									e.preventDefault();
									const f = new FormData(e.currentTarget);
									range.mutate({
										start: String(f.get("start")),
										end: String(f.get("end")),
									});
								}}
							>
								<span>{windowResult ? "Time range" : "Full recording"}</span>
								<Input
									aria-label="Start seconds"
									name="start"
									type="number"
									min={0}
									step="any"
									defaultValue={0}
									required
								/>
								<span>to</span>
								<Input
									aria-label="End seconds"
									name="end"
									type="number"
									min={0.001}
									step="any"
									defaultValue={60}
									required
								/>
								<span>seconds</span>
								<Button variant="outline" disabled={range.isPending}>
									Query range
								</Button>
								{windowResult && (
									<Button
										variant="ghost"
										type="button"
										onClick={() => {
											setWindowResult(null);
											setPage(0);
											setFramePage(0);
										}}
									>
										Full video
									</Button>
								)}
							</form>
						)}
						{range.isError && (
							<div className="error" role="alert">
								{range.error.message}
							</div>
						)}
						{tab === "Transcript" && (
							<>
								<Input
									aria-label="Search transcript"
									placeholder="Search the transcript…"
									value={search}
									onChange={(e) => {
										setSearch(e.target.value);
										setPage(0);
									}}
								/>
								<div className="transcript">
									{transcript
										.slice(viewPage * 80, (viewPage + 1) * 80)
										.map((s) => (
											<div key={s.id}>
												<button
													className="seek-button"
													onClick={() => seek(s.start_seconds)}
													aria-label={`Seek to ${duration(s.start_seconds)}`}
												>
													<Play size={11} />
													{duration(s.start_seconds)}
												</button>
												<p dir="auto">{s.text}</p>
											</div>
										))}
									{!transcript.length && (
										<div className="empty">No matching speech segments.</div>
									)}
								</div>
								<Pager
									page={viewPage}
									total={transcript.length}
									size={80}
									setPage={setPage}
								/>
							</>
						)}
						{tab === "Screen text" && (
							<div className="text-evidence">
								{context?.on_screen_text.slice(0, 300).map((t) => (
									<p dir="auto" key={t.id}>
										{t.text}
										<small>
											Observed in {t.frame_ids?.length ?? 1} frame(s)
										</small>
									</p>
								))}
								{!context?.on_screen_text.length && (
									<div className="empty">No screen text in this view.</div>
								)}
								{(context?.on_screen_text.length ?? 0) > 300 && (
									<p className="muted">
										Showing the first 300 observations. Query a time range or
										download the full context for all observations.
									</p>
								)}
							</div>
						)}
						{tab === "Frames" && (
							<>
								<div className="frames-grid">
									{context?.frames
										.slice(framePage * 24, (framePage + 1) * 24)
										.map((f) => (
											<div key={f.id}>
												<a
													href={artifact(id, f.path)}
													target="_blank"
													rel="noreferrer"
												>
													<img
														src={artifact(id, f.path)}
														alt={`Frame at ${duration(f.timestamp_seconds)}`}
														loading="lazy"
													/>
												</a>
												<button
													className="seek-button"
													onClick={() => seek(f.timestamp_seconds)}
												>
													<Play size={12} />
													{duration(f.timestamp_seconds)}
												</button>
											</div>
										))}
								</div>
								<Pager
									page={framePage}
									size={24}
									total={context?.frames.length ?? 0}
									setPage={setFramePage}
								/>
							</>
						)}
						{tab === "Files" && (
							<>
								<div className="file-downloads">
									{[
										{
											path: "context.json",
											title: "Structured context",
											hint: "JSON · all timestamps and evidence",
										},
										{
											path: "context.md",
											title: "Readable context",
											hint: "Markdown · text and frame references",
										},
										{
											path: "captions/speech.srt",
											title: "Speech captions",
											hint: "SRT · video editors and players",
										},
										{
											path: "captions/speech.vtt",
											title: "Web captions",
											hint: "WebVTT · browser playback",
										},
									].map((f) => (
										<a href={artifact(id, f.path)} key={f.path} download>
											<FileJson size={22} />
											<div>
												<strong>{f.title}</strong>
												<small>{f.hint}</small>
											</div>
											<Download size={17} />
										</a>
									))}
								</div>
								<p className="muted">
									Files remain private and are accessed through this service.
									Video previews use byte-range streaming for seeking.
								</p>
							</>
						)}
					</div>
				)}
			</section>
		</>
	);
}
