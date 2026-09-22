// Shown once each time the user enters Apex Pro while cloud AI features are
// paused (admin panel kill switch) — a lightbox, same pattern as
// WatermarkNoticeModal, not just an inline banner easy to miss. Dismissible:
// the user can still browse everything, they just can't start GO LIVE /
// Apex Image / Voice Note while this is active (enforced server-side too).
export function CloudPausedModal({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <div className="modal-backdrop">
      <div className="modal" style={{ maxWidth: 460 }}>
        <h2>⚠ Live features are temporarily paused</h2>
        <p>{message}</p>
        <p className="pro-muted">
          Everything else in Apex Cam still works normally — you can keep browsing.
        </p>
        <button type="button" className="pro-goldbtn" onClick={onDismiss}
                style={{ width: "100%", marginTop: 4 }}>
          Okay, got it
        </button>
      </div>
    </div>
  );
}
