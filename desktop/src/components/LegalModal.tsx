import { LEGAL_DOCS, type DocId } from "../legal/LegalContent";

/** Scrollable reader for a single legal document. Rendered on top of whatever
 *  opened it (the welcome lightbox or Settings). */
export function LegalModal({ doc, onClose }: { doc: DocId; onClose: () => void }) {
  const d = LEGAL_DOCS[doc];
  return (
    <div className="modal-backdrop legal-backdrop" onClick={onClose}>
      <div className="modal legal-modal" onClick={(e) => e.stopPropagation()}>
        <div className="legal-head">
          <h2>{d.title}</h2>
          <button type="button" className="legal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="legal-body">{d.body}</div>
        <button type="button" className="btn primary legal-done" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
