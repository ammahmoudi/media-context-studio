import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { request } from "../api";

type ExportFile = { path: string; bytes: number; kind: string };
type Catalog = { formats: string[]; files: ExportFile[] };

export default function RunExport({ id }: { id: string }) {
	const catalog = useQuery({ queryKey: ["exports", id], queryFn: () => request<Catalog>(`/v1/jobs/${id}/exports`) });
	const [selection, setSelection] = useState<string[] | null>(null);
	const [format, setFormat] = useState("zip");
	const files = catalog.data?.files ?? [];
	const selected = selection ?? files.filter((f) => f.kind !== "video").map((f) => f.path);
	const size = files.filter((f) => selected.includes(f.path)).reduce((sum, f) => sum + f.bytes, 0);
	function toggle(path: string) {
		setSelection(selected.includes(path) ? selected.filter((p) => p !== path) : [...selected, path]);
	}
	return <section className="panel export-panel" aria-label="Download run files">
		<h2>Download run files</h2>
		<p className="muted">Choose all files or a selection. The original video is optional. Only published results are available.</p>
		{catalog.isPending && <p>Loading available files…</p>}
		{catalog.isError && <p role="alert" className="error">{catalog.error.message}</p>}
		{catalog.data && <>
			<div className="export-controls">
				<Button variant="outline" onClick={() => setSelection(files.map((f) => f.path))}>All files</Button>
				<Button variant="outline" onClick={() => setSelection(files.filter((f) => f.kind !== "video").map((f) => f.path))}>Results only</Button>
				<Button variant="outline" onClick={() => setSelection([])}>Clear</Button>
				{["context", "captions", "frames", "video"].map((kind) => {
					const group = files.filter((f) => f.kind === kind);
					return group.length > 0 && <label key={kind}>
						<input type="checkbox" checked={group.every((f) => selected.includes(f.path))} onChange={(e) => {
							setSelection(e.target.checked ? [...new Set([...selected, ...group.map((f) => f.path)])] : selected.filter((p) => !group.some((f) => f.path === p)));
						}} />{kind === "video" ? "Original video" : kind === "context" ? "Context / report" : kind === "captions" ? "Captions" : "Frames"} ({group.length})
					</label>;
				})}
			</div>
			<details className="export-file-picker">
				<summary>Select individual files ({files.length})</summary>
				<div>{files.map((file) => <label key={file.path}>
					<input type="checkbox" checked={selected.includes(file.path)} onChange={() => toggle(file.path)} />
					<span>{file.path}</span><small>{(file.bytes / 1024).toFixed(0)} KB</small>
				</label>)}</div>
			</details>
			<form method="post" action={`/v1/jobs/${id}/exports/download`} target="_blank" rel="noopener" className="export-controls">
				<input type="hidden" name="files_json" value={JSON.stringify(selected)} />
				<label>Archive format <select name="format" value={format} onChange={(e) => setFormat(e.target.value)}>
					<option value="zip">ZIP</option><option value="tar.gz">TAR.GZ</option>
				</select></label>
				<Button disabled={!selected.length} type="submit"><Download size={15} /> Download {format.toUpperCase()}</Button>
				<span className="muted">{selected.length} selected · {(size / 1024 ** 2).toFixed(1)} MiB before compression</span>
			</form>
			<p className="muted">Downloads stream to your browser. One archive can stream at a time; large videos take longer. Any download error opens separately so this run stays visible.</p>
		</>}
	</section>;
}
