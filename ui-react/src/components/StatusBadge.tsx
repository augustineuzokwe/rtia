import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ThreadStatus } from "@/lib/types";

/**
 * Per-status color + label. Picked from Tailwind palette directly so the
 * badge stays readable in both light and dark themes (US-25 polish pass
 * will reconcile with the design tokens once they're finalised).
 */
const STATUS_CONFIG: Record<
  ThreadStatus,
  { label: string; className: string }
> = {
  running: {
    label: "Running",
    className: "bg-blue-100 text-blue-900 hover:bg-blue-100",
  },
  paused_po: {
    label: "Paused — PO",
    className: "bg-amber-100 text-amber-900 hover:bg-amber-100",
  },
  paused_review: {
    label: "Paused — Review",
    className: "bg-amber-100 text-amber-900 hover:bg-amber-100",
  },
  done: {
    label: "Done",
    className: "bg-green-100 text-green-900 hover:bg-green-100",
  },
  done_split: {
    label: "Done — Split",
    className: "bg-green-100 text-green-900 hover:bg-green-100",
  },
  error: {
    label: "Error",
    className: "bg-red-100 text-red-900 hover:bg-red-100",
  },
};

interface StatusBadgeProps {
  status: ThreadStatus;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const config = STATUS_CONFIG[status];
  return (
    <Badge
      data-testid="status-badge"
      data-status={status}
      className={cn("border-transparent", config.className, className)}
    >
      {config.label}
    </Badge>
  );
}
