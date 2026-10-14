# Kaveh image-to-3D on a Kaggle GPU with Tencent Hunyuan3D-2 (shape + texture).
# REF_B64 (the transparent-background reference PNG) is prepended by the GitHub workflow.
import base64, json, os, shutil, subprocess, sys, time, traceback

W = "/kaggle/working"
REPO = "/tmp/hy3d"
report = {"space": "kaggle:tencent/Hunyuan3D-2", "endpoint": "local-gpu", "textured": False,
          "faces": 0, "attempts": 1, "errors": [], "timings": {}}


def save():
    with open(os.path.join(W, "gen_report.json"), "w") as f:
        json.dump(report, f, indent=2)


def sh(cmd, cwd=None):
    print("+ " + cmd, flush=True)
    r = subprocess.run(cmd, shell=True, cwd=cwd)
    print("rc=%d" % r.returncode, flush=True)
    return r.returncode


t0 = time.time()
ref = os.path.join(W, "kaveh_ref.png")
with open(ref, "wb") as f:
    f.write(base64.b64decode(REF_B64))
sh("nvidia-smi")
import torch
cap = torch.cuda.get_device_capability(0) if torch.cuda.is_available() else (7, 5)
os.environ["TORCH_CUDA_ARCH_LIST"] = "%d.%d" % cap
report["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"
print("GPU", report["gpu"], "torch", torch.__version__, flush=True)

sh("git clone --depth 1 https://github.com/Tencent-Hunyuan/Hunyuan3D-2.git " + REPO)
sh("pip install -q -r requirements.txt", cwd=REPO)
sh("pip install -q --no-deps -e .", cwd=REPO)
rc_r = sh("python setup.py install", cwd=REPO + "/hy3dgen/texgen/custom_rasterizer")
rc_d = sh("python setup.py install", cwd=REPO + "/hy3dgen/texgen/differentiable_renderer")
report["build"] = {"custom_rasterizer": rc_r, "differentiable_renderer": rc_d}
report["timings"]["setup_s"] = round(time.time() - t0)
sys.path.insert(0, REPO)
os.chdir(REPO)
save()

from PIL import Image
img = Image.open(ref).convert("RGBA")
mesh = None
try:
    t1 = time.time()
    from hy3dgen.shapegen import (Hunyuan3DDiTFlowMatchingPipeline, FaceReducer,
                                  FloaterRemover, DegenerateFaceRemover)
    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained("tencent/Hunyuan3D-2")
    mesh = pipe(image=img, num_inference_steps=50, octree_resolution=380, num_chunks=20000,
                generator=torch.manual_seed(1234), output_type="trimesh")[0]
    mesh = FloaterRemover()(mesh)
    mesh = DegenerateFaceRemover()(mesh)
    mesh = FaceReducer()(mesh, max_facenum=40000)
    mesh.export(os.path.join(W, "kaveh_shape.glb"))
    report["faces"] = int(len(mesh.faces))
    report["timings"]["shape_s"] = round(time.time() - t1)
    del pipe
    torch.cuda.empty_cache()
except Exception as e:
    traceback.print_exc()
    report["errors"].append("shape: %r" % (e,))
save()

if mesh is not None:
    try:
        t2 = time.time()
        from hy3dgen.texgen import Hunyuan3DPaintPipeline
        paint = Hunyuan3DPaintPipeline.from_pretrained("tencent/Hunyuan3D-2")
        try:
            paint.enable_model_cpu_offload()
        except Exception as e:
            print("cpu offload not available:", e, flush=True)
        tmesh = paint(mesh, image=img)
        tmesh.export(os.path.join(W, "kaveh_raw_ai.glb"))
        report["textured"] = True
        report["faces"] = int(len(tmesh.faces))
        report["timings"]["paint_s"] = round(time.time() - t2)
    except Exception as e:
        traceback.print_exc()
        report["errors"].append("paint: %r" % (e,))
        shutil.copyfile(os.path.join(W, "kaveh_shape.glb"), os.path.join(W, "kaveh_raw_ai.glb"))
report["timings"]["total_s"] = round(time.time() - t0)
save()
print("REPORT", json.dumps(report), flush=True)
