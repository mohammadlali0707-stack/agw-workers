extends SceneTree

const Interaction = preload("interaction.gd")
const MockInteractable = preload("mocks/mock_interactable.gd")

var interaction: Interaction
var obj_close: MockInteractable
var obj_far: MockInteractable
var phase: int = 0
var timer: float = 0.0
var last_focused_id = "unset"
var last_prompt: String = "unset"
var focus_emit_count: int = 0

func _process(delta: float) -> bool:
	timer += delta
	match phase:
		0:
			interaction = Interaction.new()
			root.add_child(interaction)
			obj_close = MockInteractable.new("close_item", "Take Close")
			obj_close.position = Vector3(0.8, 0, 0)
			root.add_child(obj_close)
			obj_far = MockInteractable.new("far_item", "Take Far")
			obj_far.position = Vector3(1.4, 0, 0)
			root.add_child(obj_far)
			interaction.interactable_focused.connect(func(target_id, prompt):
				last_focused_id = target_id
				last_prompt = prompt
				focus_emit_count += 1
			)
			interaction.player_moved(Vector3.ZERO)
			assert(interaction.get_focused_target() == obj_close, "0.8m object must be picked over 1.4m")
			assert(last_focused_id == "close_item", "Signal target_id must match close_item")
			assert(last_prompt == "Take Close", "Signal prompt must match Take Close")
			assert(focus_emit_count == 1, "Signal must be emitted once for initial focus")
			print("[PASS] Acceptance 1: Nearest object focused (0.8m vs 1.4m)")
			interaction.player_moved(Vector3(0.1, 0, 0))
			assert(interaction.get_focused_target() == obj_close, "Target unchanged")
			assert(focus_emit_count == 1, "Must not emit when target unchanged")
			interaction.player_moved(Vector3(1.3, 0, 0))
			assert(interaction.get_focused_target() == obj_far, "Must switch to obj_far at 1.3m")
			assert(last_focused_id == "far_item" and focus_emit_count == 2, "Must emit on target change")
			interaction.player_moved(Vector3(10.0, 0, 0))
			assert(interaction.get_focused_target() == null, "Target must be null when moving >1.5m")
			assert(last_focused_id == null and last_prompt == "", "Must emit null in same frame")
			assert(focus_emit_count == 3, "Signal emitted on transition to null")
			print("[PASS] Acceptance 2: Moving >1.5m emits null focus in same frame")
			interaction.player_moved(Vector3.ZERO)
			assert(interaction.get_focused_target() == obj_close)
			timer = 0.0
			phase = 1
		1:
			var c0 = obj_close.interact_count
			interaction.interact_requested()
			assert(obj_close.interact_count == c0 + 1, "on_interact must be called once")
			interaction.interact_requested()
			assert(obj_close.interact_count == c0 + 1, "Repeated interact blocked during cooldown")
			assert(interaction.is_on_cooldown(), "Must be on cooldown after interact")
			print("[PASS] Acceptance 3: interact_requested called once and cooldown blocked repeated request")
			timer = 0.0
			phase = 2
		2:
			if interaction.is_on_cooldown():
				interaction.interact_requested()
				assert(obj_close.interact_count == 1, "Repeated interact must stay blocked while on cooldown")
			else:
				assert(timer >= 0.24, "Cooldown duration must respect cooldown_sec (0.25s)")
				interaction.interact_requested()
				assert(obj_close.interact_count == 2, "Interact succeeded after cooldown_sec")
				print("[PASS] Acceptance 3 (cooldown): Succeeded after cooldown_sec elapsed")
				phase = 3
		3:
			interaction.player_moved(Vector3(50.0, 0, 0))
			assert(interaction.get_focused_target() == null)
			interaction.interact_requested()
			assert(obj_close.interact_count == 2, "E_NO_TARGET: safely ignored when target is null")
			print("[PASS] Error handling: E_NO_TARGET safely ignored")
			var temp_obj = MockInteractable.new("temp_item", "Take Temp")
			temp_obj.position = Vector3(0.2, 0, 0)
			root.add_child(temp_obj)
			interaction.player_moved(Vector3.ZERO)
			assert(interaction.get_focused_target() == temp_obj)
			temp_obj.free()
			assert(interaction.get_focused_target() == null, "E_FREED_TARGET: focus cleared when freed")
			assert(last_focused_id == null, "E_FREED_TARGET: null emitted when freed")
			interaction.interact_requested()
			print("[PASS] Error handling: E_FREED_TARGET cleared focus and emitted null")
			print("\n=== ALL INTERACTION HEADLESS TESTS PASSED SUCCESSFULLY ===")
			quit(0)
			return true
	return false
