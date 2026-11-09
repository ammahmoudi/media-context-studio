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
};
export type Snapshot = {
	running: Job[];
	queued: Job[];
	history: Job[];
	total_history: number;
	counts: Record<string, number>;
	capacity: number;
	available_slots: number;
};
export type Health = {
	status: string;
	speech: { model: string; device: string };
	prepared_ocr_profiles: string[];
};
export type Frame = { id: string; path: string; timestamp_seconds: number };
export type Context = {
	status?: string;
	warnings?: string[];
	elapsed_seconds?: number;
	source?: { duration_seconds: number };
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
	}[];
	on_screen_text: { id: string; text: string; frame_ids?: string[] }[];
};
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
		throw new Error(message);
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
