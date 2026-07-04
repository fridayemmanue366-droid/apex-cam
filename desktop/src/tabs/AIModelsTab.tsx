import { useEffect, useState } from "react";
import { api, type ModelInfo } from "../api/client";
import { GpuSetupPanel } from "../components/GpuSetupPanel";

const KIND_LABEL: Record<ModelInfo["kind"], string> = {
  face: "Face",
  voice: "Voice",
  lipsync: "Lip sync",
  enhance: "Enhancement",
};

export function AIModelsTab() {
  const [models, setModels] = useState<ModelInfo[] | null>(null);

  useEffect(() => {
    api.models().then(setModels).catch(() => setModels(null));
  }, []);

  if (!models) return <section className="panel"><p className="muted">Backend offline.</p></section>;

  return (
    <>
      <GpuSetupPanel />
      {renderModels(models)}
    </>
  );
}

function renderModels(models: ModelInfo[]) {
  return (
    <section className="panel wide">
      <h3>Model manager</h3>
      <table className="table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Type</th>
            <th>Status</th>
            <th>Arrives</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.id}>
              <td>{m.name}</td>
              <td>{KIND_LABEL[m.kind]}</td>
              <td>
                <span className={m.status === "active" ? "pill ok" : "pill"}>
                  {m.status === "active" ? "Active" : "GPU upgrade"}
                </span>
              </td>
              <td>Phase {m.phase}</td>
              <td className="muted">{m.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        Downloading and switching real models becomes available as each engine phase lands.
      </p>
    </section>
  );
}
