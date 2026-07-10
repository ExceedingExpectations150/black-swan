import { useStore } from "@/lib/store";
import { AlertCircle, AlertTriangle, Info, Check, Trash2 } from "lucide-react";

function formatTime(ts: string) {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function AlertsPanel() {
  const alerts = useStore((s) => s.alerts);
  const dismissAlert = useStore((s) => s.dismissAlert);
  const markAllAlertsRead = useStore((s) => s.markAllAlertsRead);

  return (
    <div className="panel flex min-h-0 flex-1 flex-col p-4">
      <div className="flex items-center justify-between pb-3 border-b border-hair">
        <div className="section-title flex items-center gap-2">
          Alerts
          {alerts.filter(a => !a.read).length > 0 && (
            <span className="bg-down/20 text-down px-2 py-0.5 rounded text-[10px]">
              {alerts.filter(a => !a.read).length} New
            </span>
          )}
        </div>
        {alerts.length > 0 && (
          <button 
            onClick={markAllAlertsRead}
            className="text-[11px] text-ink3 hover:text-ink1 transition-colors flex items-center gap-1"
          >
            <Check size={12} />
            Mark all read
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto mt-3 pr-2 space-y-2 custom-scroll">
        {alerts.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs text-ink3 tracking-widest uppercase">
            No active alerts.
          </div>
        ) : (
          alerts.map((alert) => (
            <div 
              key={alert.id}
              className={`relative flex items-start gap-3 p-3 rounded-md border ${
                alert.severity === "critical" 
                  ? "bg-down/5 border-down/30" 
                  : alert.severity === "warning"
                  ? "bg-amber-500/5 border-amber-500/30"
                  : "bg-white/[0.02] border-hair"
              } ${!alert.read ? "opacity-100" : "opacity-60"}`}
            >
              <div className="shrink-0 mt-0.5">
                {alert.severity === "critical" && <AlertCircle size={16} className="text-down" />}
                {alert.severity === "warning" && <AlertTriangle size={16} className="text-amber-500" />}
                {alert.severity === "info" && <Info size={16} className="text-ink2" />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-medium text-[13px] text-ink1 truncate">
                    {alert.title}
                  </div>
                  <div className="text-[10px] text-ink3 whitespace-nowrap shrink-0">
                    {formatTime(alert.ts)}
                  </div>
                </div>
                <div className="text-[12px] text-ink2 mt-1 leading-snug">
                  {alert.message}
                </div>
              </div>
              <button 
                onClick={() => dismissAlert(alert.id)}
                className="shrink-0 p-1 text-ink3 hover:text-ink1 transition-colors"
                title="Dismiss alert"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
