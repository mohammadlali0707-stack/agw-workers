class_name PerformanceProfile extends Node

const E_PROFILE_FAIL: String = "E_PROFILE_FAIL"
signal performance_settings_applied(profile: Dictionary)

@export var target_fps: int = 60
@export var render_scale: float = 0.8
@export var shadow_atlas_size: int = 1024
@export var msaa: int = 0
var last_error: String = ""

func _ready() -> void:
	apply_settings()

func get_active_profile() -> Dictionary:
	return {"target_fps": target_fps, "render_scale": render_scale, "shadow_atlas_size": shadow_atlas_size, "msaa": msaa}

func validate_params(p_fps: int, p_scale: float, p_shadow: int, p_msaa: int, vp: Viewport) -> bool:
	if not vp or p_fps < 0 or is_nan(p_scale) or is_inf(p_scale) or p_scale <= 0.0 or p_scale > 2.0 or p_shadow < 0 or p_msaa < 0 or p_msaa > 4:
		return false
	return true

func apply_settings(new_params: Dictionary = {}) -> bool:
	var c_fps: int = int(new_params.get("target_fps", target_fps))
	var c_scale: float = float(new_params.get("render_scale", render_scale))
	var c_shadow: int = int(new_params.get("shadow_atlas_size", shadow_atlas_size))
	var c_msaa: int = int(new_params.get("msaa", msaa))
	var vp = get_viewport()
	if not validate_params(c_fps, c_scale, c_shadow, c_msaa, vp):
		last_error = E_PROFILE_FAIL
		push_error("%s: invalid params or viewport unavailable" % E_PROFILE_FAIL)
		return false
	last_error = ""; target_fps = c_fps; render_scale = c_scale; shadow_atlas_size = c_shadow; msaa = c_msaa
	Engine.max_fps = target_fps
	vp.scaling_3d_scale = render_scale
	vp.msaa_3d = msaa as Viewport.MSAA
	vp.positional_shadow_atlas_size = shadow_atlas_size
	RenderingServer.directional_shadow_atlas_set_size(shadow_atlas_size, true)
	var profile = get_active_profile()
	performance_settings_applied.emit(profile)
	var bus = get_node_or_null("/root/EventBus")
	if bus:
		if bus.has_signal("performance_settings_applied"): bus.emit_signal("performance_settings_applied", profile)
		elif bus.has_method("emit_event"): bus.call("emit_event", "performance_settings_applied", profile)
	return true

func apply_profile(params: Dictionary = {}) -> bool:
	return apply_settings(params)
