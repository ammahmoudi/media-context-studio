import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ArrowUpRight, ChevronLeft, ChevronRight } from "lucide-react";
import type { Job } from "../api";

import { runPath } from "../navigation";
import type { Navigate } from "../navigation";
export function Link({
	to,
	navigate,
	children,
	className,
}: {
	to: string;
	navigate: Navigate;
	children: React.ReactNode;
	className?: string;
}) {
	return (
		<a
			href={to}
			className={className}
			onClick={(e) => {
				if (!e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0) {
					e.preventDefault();
					navigate(to);
				}
			}}
		>
			{children}
		</a>
	);
}
export function Status({ status }: { status: string }) {
	return (
		<Badge variant="outline" className={`status ${status}`}>
			{status === "running" && <span className="live-dot" />}
			{status}
		</Badge>
	);
}
export function Stage({ job }: { job: Job }) {
	const match = job.stage.match(/^(ocr|speech) ([\d.]+)\/([\d.]+)/);
	const fraction = match
		? Math.min(100, (Number(match[2]) / Number(match[3])) * 100)
		: null;
	return (
		<div className="stage">
			<div className="row-between">
				<span>{job.stage}</span>
				{fraction != null && <small>{Math.round(fraction)}% of stage</small>}
			</div>
			{fraction != null && (
				<progress
					aria-label={`${match![1]} progress`}
					max={100}
					value={fraction}
				/>
			)}
		</div>
	);
}
export function Pager({
	page,
	total,
	size,
	setPage,
}: {
	page: number;
	total: number;
	size: number;
	setPage: (p: number) => void;
}) {
	return (
		<div className="pagination">
			<span>
				{total
					? `${page * size + 1}–${Math.min((page + 1) * size, total)} of ${total}`
					: "No items"}
			</span>
			<div>
				<Button
					size="icon-sm"
					variant="outline"
					disabled={page === 0}
					aria-label="Previous page"
					onClick={() => setPage(page - 1)}
				>
					<ChevronLeft size={16} />
				</Button>
				<Button
					size="icon-sm"
					variant="outline"
					disabled={(page + 1) * size >= total}
					aria-label="Next page"
					onClick={() => setPage(page + 1)}
				>
					<ChevronRight size={16} />
				</Button>
			</div>
		</div>
	);
}
export function RunLink({ job, navigate }: { job: Job; navigate: Navigate }) {
	return (
		<Link to={runPath(job.id)} navigate={navigate} className="run-link">
			<strong dir="auto">{job.source_file}</strong>
			<span>
				{job.id.slice(0, 8)}
				<ArrowUpRight size={13} />
			</span>
		</Link>
	);
}
