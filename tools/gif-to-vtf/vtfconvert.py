import io

def gif_to_vtf(data, size=64, max_frames=64):
	from PIL import Image, ImageSequence
	from srctools.vtf import VTF, ImageFormats, VTFFlags

	image = Image.open(io.BytesIO(data))
	if image.width * image.height > 4096 * 4096 or getattr(image, "n_frames", 1) > 1000:
		raise ValueError("image is too large")

	frames = []
	durations = []
	for frame in ImageSequence.Iterator(image):
		durations.append(frame.info.get("duration") or 100)
		frames.append(frame.convert("RGBA").resize((size, size), Image.LANCZOS))

	total = sum(durations)
	step = -(-len(frames) // max_frames)
	frames = frames[::step]
	fps = max(1, min(60, round(len(frames) * 1000 / total)))

	flags = (
		VTFFlags.EIGHTBITALPHA
		| VTFFlags.NO_MIP
		| VTFFlags.NO_LOD
		| VTFFlags.CLAMP_S
		| VTFFlags.CLAMP_T
	)
	vtf = VTF(size, size, version=(7, 2), frames=len(frames), fmt=ImageFormats.DXT5, flags=flags)
	for i, frame in enumerate(frames):
		vtf.get(frame=i).copy_from(frame.tobytes(), ImageFormats.RGBA8888)

	out = io.BytesIO()
	vtf.save(out)
	return out.getvalue(), fps
