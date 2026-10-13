import { forwardRef, useImperativeHandle, useRef, useState } from "react";

// Owner decision (2026-10-13): Apex Pro uploads (Lucy reference photo, Lucy
// Image, Lucy Video) can ONLY come from SafeShare — Apex Cam must not see the
// rest of the PC, and it markets SafeShare, the owner's second app. The local
// backend (/safeshare/files, /safeshare/file) only ever reads inside
// Downloads\SafeShare; this replaces the hidden <input type="file">s and keeps
// the same `.click()` shape so callers didn't have to change.

const API = "http://127.0.0.1:8790";

export interface PickerHandle { click: () => void }

interface SSFile { path: string; name: string; folder: string; size: number; modified: number }
interface Listing { installed: boolean; folder: string; download_url: string; files: SSFile[] }

type Kind = "image" | "video" | "audio";
const WORD: Record<Kind, [string, string]> = {
  image: ["photo", "photos"], video: ["video", "videos"], audio: ["voice clip", "voice clips"],
};
const ACCEPT: Record<Kind, string> = { image: "image/*", video: "video/*", audio: "audio/*" };

export const SafeSharePicker = forwardRef<PickerHandle, {
  kind: Kind;
  onPick: (f: File | undefined) => void;
}>(function SafeSharePicker({ kind, onPick }, ref) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Listing | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const pcInput = useRef<HTMLInputElement>(null);
  const [one, many] = WORD[kind];

  // "Add from this PC…" (@safeshare's idea): the normal Windows dialog, but the
  // chosen file is COPIED into SafeShare's Inbox first, so it still goes
  // through SafeShare and nobody gets stuck without their phone.
  const addFromPc = async (f: File | undefined) => {
    if (pcInput.current) pcInput.current.value = "";
    if (!f) return;
    setBusy("__import");
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`${API}/safeshare/import`, { method: "POST", body: fd });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Couldn't add that file");
      onPick(f);
      setOpen(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't add that file");
    } finally {
      setBusy(null);
    }
  };

  const load = () => {
    setErr(null);
    fetch(`${API}/safeshare/files?kind=${kind}`).then((r) => r.json())
      .then(setData).catch(() => setErr("Couldn't read your SafeShare folder."));
  };
  useImperativeHandle(ref, () => ({ click: () => { setOpen(true); load(); } }));

  const choose = async (f: SSFile) => {
    setBusy(f.path);
    try {
      const r = await fetch(`${API}/safeshare/file?path=${encodeURIComponent(f.path)}`);
      if (!r.ok) throw new Error();
      const blob = await r.blob();
      const fallback = kind === "video" ? "video/mp4" : kind === "audio" ? "audio/wav" : "image/jpeg";
      onPick(new File([blob], f.name, { type: blob.type || fallback }));
      setOpen(false);
    } catch {
      setErr("Couldn't open that file.");
    } finally {
      setBusy(null);
    }
  };

  if (!open) return null;
  const files = data?.files ?? [];
  return (
    <div className="modal-backdrop" onClick={() => setOpen(false)}
         style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 1000,
                  display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div className="pro-card" onClick={(e) => e.stopPropagation()}
           style={{ width: "min(720px, 92vw)", maxHeight: "80vh", display: "flex", flexDirection: "column" }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>Choose a {one} from SafeShare</h3>
          <button type="button" className="pro-chip" onClick={() => setOpen(false)}>✕</button>
        </div>
        {data && !data.installed ? (
          <div style={{ marginTop: 12 }}>
            <p className="pro-muted">
              Apex Pro uses your files from <b>SafeShare</b> — our free-to-try app that moves photos and
              videos from your phone to your PC over Wi-Fi, in full quality. Install it, send your
              {" "}{one} to this PC, then come back here.
            </p>
            <a className="pro-goldbtn" href={data.download_url} target="_blank" rel="noreferrer">
              ⬇ Get SafeShare
            </a>
          </div>
        ) : (
          <>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center", margin: "8px 0" }}>
              <p className="pro-muted" style={{ margin: 0 }}>
                Showing {many} in your SafeShare folder. Send more from your phone with SafeShare.
              </p>
              <button type="button" className="pro-chip" disabled={!!busy}
                      onClick={() => pcInput.current?.click()}>
                {busy === "__import" ? "Adding…" : "＋ Add from this PC…"}
              </button>
              <input ref={pcInput} type="file" accept={ACCEPT[kind]} hidden
                     onChange={(e) => addFromPc(e.target.files?.[0])} />
            </div>
            <div style={{ overflowY: "auto", flex: 1 }}>
              {!data ? <p className="pro-muted">Loading…</p>
               : files.length === 0 ? (
                <p className="pro-muted">No {many} in SafeShare yet.{" "}
                  <a href={data.download_url} target="_blank" rel="noreferrer">How to send files with SafeShare</a></p>
              ) : (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 10 }}>
                  {files.map((f) => (
                    <button type="button" key={f.path} className="pro-photo-slot small" disabled={!!busy}
                            title={f.folder ? `${f.folder}/${f.name}` : f.name}
                            onClick={() => choose(f)}
                            style={{ height: 120, padding: 0, overflow: "hidden", position: "relative" }}>
                      {kind === "image"
                        ? <img src={`${API}/safeshare/file?path=${encodeURIComponent(f.path)}`} alt=""
                               loading="lazy" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                        : <span style={{ fontSize: 30 }}>{kind === "audio" ? "🎙" : "🎬"}</span>}
                      <span style={{ position: "absolute", left: 0, right: 0, bottom: 0, fontSize: 11,
                                     background: "rgba(0,0,0,0.6)", padding: "2px 4px", whiteSpace: "nowrap",
                                     overflow: "hidden", textOverflow: "ellipsis" }}>
                        {busy === f.path ? "Opening…" : f.name}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
        {err && <p className="error">{err}</p>}
      </div>
    </div>
  );
});
