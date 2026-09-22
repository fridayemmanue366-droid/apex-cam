// Shown once each time the user enters Apex Pro while their account is
// unlicensed — a clear, upfront heads-up (not a silent surprise discovered
// only by looking at the output) that Lucy Realtime and local face swap
// output carries an "AI-GENERATED" watermark until the one-time $10 license
// is bought or granted. Re-shows every time this component mounts (i.e.
// every time the user navigates back into Apex Pro), which is intentional —
// it should keep reminding them, not just once ever.
export function WatermarkNoticeModal({
  priceLabel, onBuy, buying, onDismiss,
}: {
  priceLabel: string;
  onBuy: () => void;
  buying: boolean;
  onDismiss: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal" style={{ maxWidth: 460 }}>
        <h2>Your output carries a watermark</h2>
        <p>
          Until you remove it, Lucy Realtime and local face swap burn an{" "}
          <strong>"AI-GENERATED — APEX CAM"</strong> bar across the bottom of every video and
          photo you produce.
        </p>
        <p className="pro-muted">
          Remove it once, for good — <strong>{priceLabel}, a one-time payment</strong>, not a
          subscription.
        </p>
        <button type="button" className="pro-goldbtn" disabled={buying} onClick={onBuy}
                style={{ width: "100%", marginTop: 4 }}>
          {buying ? "Opening…" : `✦ Remove watermark — ${priceLabel} once`}
        </button>
        <button type="button" className="pro-linkbtn" onClick={onDismiss}
                style={{ display: "block", margin: "14px auto 0" }}>
          Continue with the watermark for now
        </button>
      </div>
    </div>
  );
}
