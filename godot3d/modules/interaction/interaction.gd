class_name Interaction extends Area3D

const E_NO_TARGET: String = "E_NO_TARGET"
const E_FREED_TARGET: String = "E_FREED_TARGET"

signal interactable_focused(target_id, prompt_text)

@export var interact_radius: float = 1.5:
	set(value):
		interact_radius = value
		if sphere_shape:
			sphere_shape.radius = value

@export var cooldown_sec: float = 0.25

var collision_shape: CollisionShape3D
var sphere_shape: SphereShape3D
var focused_target: Node3D = null
var cooldown_remaining: float = 0.0

func _init() -> void:
	collision_shape = CollisionShape3D.new()
	sphere_shape = SphereShape3D.new()
	sphere_shape.radius = interact_radius
	collision_shape.shape = sphere_shape
	add_child(collision_shape)

func _ready() -> void:
	var bus = get_node_or_null("/root/EventBus")
	if bus:
		if bus.has_signal("player_moved"): bus.player_moved.connect(on_player_moved)
		if bus.has_signal("interact_requested"): bus.interact_requested.connect(on_interact_requested)

func _process(delta: float) -> void:
	if cooldown_remaining > 0.0:
		cooldown_remaining = max(0.0, cooldown_remaining - delta)
	if focused_target != null and not is_instance_valid(focused_target):
		_set_focused_target(null)

func on_player_moved(pos: Vector3) -> void:
	global_position = pos
	if focused_target != null and not is_instance_valid(focused_target):
		_set_focused_target(null)
	if not is_inside_tree():
		return
	var nearest: Node3D = null
	var min_dist: float = interact_radius + 0.0001
	for node in get_tree().get_nodes_in_group("interactable"):
		if not is_instance_valid(node) or not (node is Node3D):
			continue
		var d = pos.distance_to(node.global_position)
		if d <= interact_radius and d < min_dist:
			min_dist = d
			nearest = node
	if nearest != focused_target:
		_set_focused_target(nearest)

func on_interact_requested() -> void:
	if focused_target != null and not is_instance_valid(focused_target):
		_set_focused_target(null)
	if focused_target == null or cooldown_remaining > 0.0:
		return
	cooldown_remaining = cooldown_sec
	if focused_target.has_method("on_interact"):
		focused_target.on_interact()

func _set_focused_target(node: Node3D) -> void:
	if focused_target != null and is_instance_valid(focused_target):
		if focused_target.tree_exited.is_connected(_on_target_tree_exited):
			focused_target.tree_exited.disconnect(_on_target_tree_exited)
	focused_target = node
	if focused_target != null:
		if not focused_target.tree_exited.is_connected(_on_target_tree_exited):
			focused_target.tree_exited.connect(_on_target_tree_exited)
		var tid = focused_target.get("target_id")
		if tid == null or tid == "":
			tid = focused_target.name
		var prompt: String = str(focused_target.get("prompt_text")) if focused_target.get("prompt_text") != null else ""
		interactable_focused.emit(tid, prompt)
	else:
		interactable_focused.emit(null, "")

func _on_target_tree_exited() -> void:
	_set_focused_target(null)

func get_focused_target() -> Node3D:
	if focused_target != null and not is_instance_valid(focused_target):
		_set_focused_target(null)
	return focused_target

func is_on_cooldown() -> bool:
	return cooldown_remaining > 0.0

func player_moved(pos: Vector3) -> void: on_player_moved(pos)
func interact_requested() -> void: on_interact_requested()
