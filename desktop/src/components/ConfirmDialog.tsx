import { useMedia } from "../context/MediaContext";

// Generic confirm prompt driven by MediaContext's pendingConfirm. Currently
// used to ask before restarting the camera for a device/resolution change.
export function ConfirmDialog() {
  const { pendingConfirm, resolvePendingConfirm } = useMedia();
  if (!pendingConfirm) return null;

  return (
    <div className="modal-backdrop">
      <div className="modal confirm">
        <p>{pendingConfirm.message}</p>
        <div className="row confirm-actions">
          <button type="button" className="btn" onClick={() => resolvePendingConfirm(false)}>
            Cancel &amp; keep it
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={() => resolvePendingConfirm(true)}
          >
            {pendingConfirm.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
