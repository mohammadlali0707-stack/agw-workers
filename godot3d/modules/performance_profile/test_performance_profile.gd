extends SceneTree

const PerformanceProfile = preload("performance_profile.gd")
var phase: int = 0
var bus_received_profile: Dictionary = {}
var bus_event_count: int = 0

func _process(_delta: float) -> bool:
	if phase != 0: return false
	phase = 1
	var event_bus = Node.new(); event_bus.name = "EventBus"
	event_bus.add_user_signal("performance_settings_applied", [{"name": "profile", "type": TYPE_DICTIONARY}])
	root.add_child(event_bus)
	event_bus.connect("performance_settings_applied", func(p: Dictionary): bus_received_profile = p; bus_event_count += 1)
	var perf = PerformanceProfile.new(); root.add_child(perf)
	var vp = root
	assert(Engine.max_fps == 60 and abs(vp.scaling_3d_scale - 0.8) < 0.001 and vp.positional_shadow_atlas_size == 1024 and vp.msaa_3d == 0, "Acceptance 1 failed")
	print("[PASS] Acceptance 1: Default params applied correctly")
	assert(bus_event_count == 1 and bus_received_profile.get("target_fps") == 60 and abs(bus_received_profile.get("render_scale", 0.0) - 0.8) < 0.001 and bus_received_profile.get("shadow_atlas_size") == 1024 and bus_received_profile.get("msaa") == 0, "Acceptance 2 failed")
	print("[PASS] Acceptance 2: Integration test - performance_settings_applied received on EventBus")
	assert(perf.apply_settings({"target_fps": 30, "render_scale": 0.5, "shadow_atlas_size": 2048, "msaa": 1}), "Custom params failed")
	assert(Engine.max_fps == 30 and abs(vp.scaling_3d_scale - 0.5) < 0.001 and vp.positional_shadow_atlas_size == 2048 and vp.msaa_3d == 1 and bus_event_count == 2, "Custom profile values check failed")
	var prev_count = bus_event_count
	for invalid_p in [{"render_scale": -0.5}, {"target_fps": -10}, {"shadow_atlas_size": -100}, {"msaa": -1}]:
		assert(not perf.apply_settings(invalid_p) and perf.last_error == perf.E_PROFILE_FAIL and bus_event_count == prev_count, "Acceptance 3 invalid param check failed")
	var unparented = PerformanceProfile.new()
	assert(not unparented.apply_settings() and unparented.last_error == PerformanceProfile.E_PROFILE_FAIL, "Unparented check failed")
	unparented.free()
	print("[PASS] Acceptance 3: Invalid params yield E_PROFILE_FAIL without emitting event\n\n=== ALL PERFORMANCE PROFILE HEADLESS TESTS PASSED SUCCESSFULLY ===")
	quit(0)
	return true
