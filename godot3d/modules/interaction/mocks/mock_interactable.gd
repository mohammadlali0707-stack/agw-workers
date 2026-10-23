class_name MockInteractable extends Node3D

@export var target_id: String = ""
@export var prompt_text: String = ""
var interact_count: int = 0

func _init(p_target_id: String = "", p_prompt: String = "") -> void:
	target_id = p_target_id
	prompt_text = p_prompt
	add_to_group("interactable")

func _ready() -> void:
	if not is_in_group("interactable"):
		add_to_group("interactable")

func on_interact() -> void:
	interact_count += 1
