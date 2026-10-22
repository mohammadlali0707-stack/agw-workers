class_name MockZoneEmitter extends Node3D

signal zone_entered(zone_id: String)
signal player_moved(pos: Vector3)

var zone_id: String = ""
var current_distance: float = 0.0
var max_distance: float = 20.0
var volume_db: float = 0.0
var is_in_range: bool = false

func update_distance(dist: float, max_dist: float) -> void:
	current_distance = dist
	max_distance = max_dist
	is_in_range = dist <= max_dist
	volume_db = lerp(0.0, -80.0, clamp(dist / max_dist, 0.0, 1.0)) if is_in_range else -80.0

func trigger_zone(id: String = "") -> void:
	zone_entered.emit(id if id != "" else zone_id)

func trigger_move(pos: Vector3) -> void:
	player_moved.emit(pos)
