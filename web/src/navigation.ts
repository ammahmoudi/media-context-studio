export type Navigate = (path: string) => void;
export const active = (status?: string) =>
	["uploading", "queued", "running"].includes(status ?? "");
export const date = (value?: string) =>
	value
		? new Date(value).toLocaleString([], {
				month: "short",
				day: "numeric",
				hour: "2-digit",
				minute: "2-digit",
			})
		: "—";
export const runPath = (id: string) => `/ui/jobs/${encodeURIComponent(id)}`;
