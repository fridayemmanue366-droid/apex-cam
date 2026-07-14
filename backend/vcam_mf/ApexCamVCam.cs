// Apex Cam -> Windows 11 Media Foundation virtual camera bridge.
//
// Uses the LocosLab.VirtualCamera .NET assembly (installed to the GAC by the
// LocosLab Virtual Camera installer) to publish a camera named "Apex Cam" that
// Media-Foundation apps (WhatsApp, Teams, the Windows Camera app) can see.
//
// The actual video comes from Apex Cam's Python backend, which writes RGB32
// (BGRX) frames into a shared memory-mapped file. This process just reads the
// latest frame out of that shared memory and hands it to the frame server.
//
// Build with the C# compiler that ships with Windows (no Visual Studio):
//   csc /target:exe /platform:x64 /out:ApexCamVCam.exe \
//       /reference:"<GAC path>\LocosLab.VirtualCamera.dll" ApexCamVCam.cs
using System;
using System.IO;
using System.IO.MemoryMappedFiles;
using LocosLab.VirtualCamera;

namespace ApexCam
{
    internal class Program
    {
        // 540p keeps 16:9 but pushes ~45% fewer bytes through the frame pipe than
        // 720p — smoother delivery into a WhatsApp call, which downscales anyway.
        public const int WIDTH = 960;
        public const int HEIGHT = 540;
        public const int FRAME_BYTES = WIDTH * HEIGHT * 4;   // RGB32
        // Session-local shared memory shared with the Python backend.
        public const string MMF_NAME = "Local\\ApexCamFrame";

        [MTAThread]
        static int Main(string[] args)
        {
            try
            {
                using (var mmf = MemoryMappedFile.CreateOrOpen(MMF_NAME, FRAME_BYTES))
                using (var acc = mmf.CreateViewAccessor(0, FRAME_BYTES))
                {
                    var cam = new VirtualCamera("Apex Cam", WIDTH, HEIGHT, new ApexFactory(acc));
                    uint r = cam.Start();
                    if (r != 0)
                    {
                        Console.Error.WriteLine("APEXCAM_START_FAILED " + r.ToString("x"));
                        return 1;
                    }
                    Console.WriteLine("APEXCAM_STARTED");
                    Console.Out.Flush();
                    // Run until the parent closes our stdin or sends "stop".
                    string line;
                    while ((line = Console.ReadLine()) != null)
                    {
                        if (line.Trim() == "stop") break;
                    }
                    cam.Stop();
                    return 0;
                }
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("APEXCAM_ERROR " + ex);
                return 2;
            }
        }
    }

    internal class ApexFactory : FrameGeneratorFactory
    {
        private readonly MemoryMappedViewAccessor _acc;
        public ApexFactory(MemoryMappedViewAccessor acc) { _acc = acc; }
        public FrameGenerator CreateFrameGenerator(ushort width, ushort height)
        {
            return new ApexGenerator(_acc);
        }
    }

    // Pull-model generator: on each request, copy the latest frame the Python
    // backend wrote into shared memory. If nothing has been written yet the buffer
    // is zeroed (black), which is fine until the first real frame arrives.
    internal class ApexGenerator : FrameGeneratorBase
    {
        private readonly MemoryMappedViewAccessor _acc;
        private readonly byte[] _buf = new byte[Program.FRAME_BYTES];

        public ApexGenerator(MemoryMappedViewAccessor acc) { _acc = acc; }

        public override void CreateFrame(long time, BinaryWriter writer, uint bytes)
        {
            // Pace frames as the framework expects — bad timing makes the consuming
            // app (WhatsApp) stutter or hang.
            ThrottleFrameRate(time);
            int n = (int)Math.Min((uint)_buf.Length, bytes);
            // NEVER throw here: if the shared memory read fails (e.g. the app is
            // restarting), hand back a black frame instead of breaking the protocol,
            // which would freeze the camera and hang WhatsApp.
            try { _acc.ReadArray(0, _buf, 0, n); }
            catch { Array.Clear(_buf, 0, n); }
            try
            {
                writer.Write(_buf, 0, n);
                for (uint i = (uint)n; i < bytes; i++) writer.Write((byte)0);
            }
            catch { /* pipe closing — nothing we can safely do */ }
        }
    }
}
