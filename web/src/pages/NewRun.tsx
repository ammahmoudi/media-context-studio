import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileVideo, LoaderCircle, Plus, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { request } from "../api";
import type { Health, Job, Snapshot } from "../api";
import { runPath } from "../navigation";
import type { Navigate } from "../navigation";

export default function NewRun({
	health,
	snapshot,
	navigate,
}: {
	health: Health;
	snapshot?: Snapshot;
	navigate: Navigate;
}) {
	const [mode, setMode] = useState(health.allow_path ? "path" : "upload");
	const [queueChoice, setQueueChoice] = useState("");
	const preferredQueue =
		queueChoice ||
		(snapshot?.workers.some((w) => w.online && w.queues.includes("gpu"))
			? "gpu"
			: "default");
	const client = useQueryClient();
	const submit = useMutation({
		mutationFn: async (form: HTMLFormElement) => {
			const f = new FormData(form),
				queue = String(f.get("queue"));
			const options = {
				language: f.get("language") || null,
				ocr_language: f.get("ocr_language"),
				speech: f.has("speech"),
				ocr: f.has("ocr"),
				ocr_deduplicate: f.has("deduplicate"),
				sample_interval_seconds: Number(f.get("interval")),
				max_frames: Number(f.get("max_frames")),
			};
			if (mode === "upload") {
				const body = new FormData();
				body.append("file", f.get("file") as File);
				body.append("options_json", JSON.stringify(options));
				body.append("queue", queue);
				return request<Job>("/v1/jobs/upload", { method: "POST", body });
			}
			return request<Job>("/v1/jobs", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					path: String(f.get("path")).trim(),
					options,
					queue,
				}),
			});
		},
		onSuccess: (r) => {
			client.invalidateQueries();
			navigate(runPath(r.id));
		},
	});
	return (
		<>
			<div className="intro">
				<div>
					<div className="eyebrow">NEW EXTRACTION</div>
					<h1>Give your next video some context.</h1>
					<p>
						Save a recording, choose what to extract, and let an available
						worker take it from there.
					</p>
				</div>
			</div>
			<div className="submission-layout">
				<section className="panel new-run">
					<div className="panel-heading">
						<h2>
							<FileVideo size={18} />
							Video & extraction settings
						</h2>
						<span className="muted">
							{snapshot?.available_slots ?? "—"} slots available
						</span>
					</div>
					<form
						onSubmit={(e) => {
							e.preventDefault();
							submit.mutate(e.currentTarget);
						}}
					>
						{health.allow_path && (
							<div className="mode-switch">
								{["path", "upload"].map((m) => (
									<button
										type="button"
										className={mode === m ? "chosen" : ""}
										key={m}
										onClick={() => setMode(m)}
									>
										{m === "path" ? "Local file path" : "Upload a recording"}
									</button>
								))}
							</div>
						)}
						{mode === "path" ? (
							<label>
								Full video path
								<Input
									name="path"
									required
									placeholder="C:\Videos\lecture.mp4"
								/>
								<small>
									The source is copied into durable storage before the job is
									queued.
								</small>
							</label>
						) : (
							<label className="upload-box">
								<Upload size={28} />
								<span>Choose your recording</span>
								<Input type="file" name="file" accept="video/*,.mkv" required />
								<small>
									Input and extracted artifacts stay in private storage.
								</small>
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
								Screen text language
								<select name="ocr_language" defaultValue="en">
									{(health.prepared_ocr_profiles.length
										? health.prepared_ocr_profiles
										: ["en"]
									).map((p) => (
										<option value={p} key={p}>
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
								Frame interval (seconds)
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
						<label className="queue-choice">
							Processing queue
							<select
								name="queue"
								value={preferredQueue}
								onChange={(e) => setQueueChoice(e.target.value)}
							>
								<option value="default">Default · next matching worker</option>
								<option value="cpu">CPU workers</option>
								<option value="gpu">GPU workers</option>
							</select>
							<small>
								A run waits until a worker listening to this queue is available.
							</small>
						</label>
						<div className="checks">
							<label>
								<input name="speech" type="checkbox" defaultChecked />
								Speech transcription & captions
							</label>
							<label>
								<input name="ocr" type="checkbox" defaultChecked />
								Screen text (OCR)
							</label>
							<label>
								<input name="deduplicate" type="checkbox" defaultChecked />
								Reuse unchanged text between frames
							</label>
						</div>
						{submit.isError && (
							<div role="alert" className="error">
								{submit.error.message}
							</div>
						)}
						<Button
							className="submit-button"
							type="submit"
							disabled={submit.isPending || snapshot?.available_slots === 0}
						>
							{submit.isPending ? (
								<LoaderCircle size={17} className="spin" />
							) : (
								<Plus size={17} />
							)}{" "}
							{submit.isPending ? "Saving input…" : "Create run"}
						</Button>
					</form>
				</section>
				<aside className="submission-guide">
					<div className="eyebrow">WHAT HAPPENS NEXT</div>
					<ol>
						<li>
							<strong>Store once</strong>
							<p>Identical source videos share a content-addressed object.</p>
						</li>
						<li>
							<strong>Join the queue</strong>
							<p>A matching worker claims your run and reports its progress.</p>
						</li>
						<li>
							<strong>Explore the result</strong>
							<p>
								Watch captions, inspect frames, and download structured
								evidence.
							</p>
						</li>
					</ol>
					<p className="muted">
						Smaller frame intervals capture more screen changes and increase
						processing time. Captions and OCR remain machine-generated evidence
						that may need correction.
					</p>
				</aside>
			</div>
		</>
	);
}
