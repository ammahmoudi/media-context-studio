export type MediaSource = {
	media_type?: "video" | "audio" | "image";
	duration_seconds: number;
	width?: number;
	height?: number;
};
export type Job = {
	id: string;
	status: string;
	stage: string;
	source_file: string;
	created_at: string;
	updated_at?: string;
	started_at?: string;
	elapsed_seconds?: number;
	error?: string;
	queue_position?: number;
	queue?: string;
	worker_id?: string;
	attempt?: number;
	video_available?: boolean;
	media_available?: boolean;
	input_bytes?: number;
	finished_at?: string;
	source?: MediaSource;
	artifacts?: { path: string; bytes: number }[];
	resources?: { peak_memory_mb: number; peak_gpu_memory_mb?: number; memory_limit_mb: number };
	error_code?: string;
};
export type Snapshot = {
	running: Job[];
	queued: Job[];
	history: Job[];
	total_history: number;
	counts: Record<string, number>;
	capacity: number;
	available_slots: number;
	uploading: Job[];
	workers: Worker[];
	database: string;
	storage: string;
};
export type Worker = {
	id: string;
	online: boolean;
	hostname: string;
	queues: string[];
	device: string;
	model: string;
	job_id?: string;
	memory_mb: number;
	cpu_percent: number;
	cpu_threads: number;
	gpu_memory_used_mb?: number;
	gpu_memory_total_mb?: number;
	gpu_utilization_percent?: number;
	last_seen_seconds_ago: number;
	stopping: boolean;
	blocked_reason?: string;
	memory_available_mb?: number;
	memory_limit_mb?: number;
	memory_scope?: string;
	peak_memory_mb?: number;
	resource_domain?: string;
	max_parallel?: number;
	scratch_free_mb?: number;
};
export type Health = {
	status: string;
	speech: { model: string; device: string };
	prepared_ocr_profiles: string[];
	auth_required: boolean;
	allow_path: boolean;
	worker_mode: string;
	database: string;
	storage: string;
};
export type Frame = { id: string; path: string; timestamp_seconds: number };
export type Context = {
	status?: string;
	warnings?: string[];
	elapsed_seconds?: number;
	source?: MediaSource;
	stages?: Record<
		string,
		{ status: string; elapsed_seconds?: number; error?: string }
	>;
	frames: Frame[];
	speech_segments: {
		id: string;
		start_seconds: number;
		end_seconds: number;
		text: string;
		language?: string;
	}[];
	on_screen_text: { id: string; text: string; frame_ids?: string[] }[];
};
export class ApiError extends Error {
	status: number;
	constructor(message: string, status: number) {
		super(message);
		this.status = status;
	}
}
export async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await fetch(url, init);
	if (!response.ok) {
		let message = `${response.status} ${response.statusText}`;
		try {
			const body = await response.json();
			message =
				typeof body.detail === "string"
					? body.detail
					: JSON.stringify(body.detail ?? body);
		} catch {
			/* Preserve HTTP error if the response isn't JSON. */
		}
		throw new ApiError(message, response.status);
	}
	return response.json();
}
export const duration = (s?: number) =>
	s == null
		? "—"
		: `${Math.floor(s / 60)}m ${Math.floor(s % 60)
				.toString()
				.padStart(2, "0")}s`;
export const artifact = (id: string, path: string) =>
	`/v1/jobs/${encodeURIComponent(id)}/artifacts/${path}`;
