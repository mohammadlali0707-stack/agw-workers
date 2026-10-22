class_name AmbienceAudio extends Node3D

const E_UNKNOWN_ZONE: String = "E_UNKNOWN_ZONE"
@export var crossfade_time: float = 1.5
@export var default_volume_db: float = -6.0
@export var mute_volume_db: float = -80.0
@export var max_distance_3d: float = 20.0

var player_a: AudioStreamPlayer
var player_b: AudioStreamPlayer
var emitters_3d: Node3D
var active_player: AudioStreamPlayer
var reserve_player: AudioStreamPlayer
var current_zone_id: String = ""
var zone_streams: Dictionary = {}
var listener_position: Vector3 = Vector3.ZERO
var active_tween: Tween

func _init() -> void:
	if AudioServer.get_bus_index("Ambience") == -1:
		AudioServer.add_bus()
		AudioServer.set_bus_name(AudioServer.bus_count - 1, "Ambience")
	player_a = _create_player("PlayerA")
	player_b = _create_player("PlayerB")
	emitters_3d = Node3D.new()
	emitters_3d.name = "emitters_3d"
	add_child(emitters_3d)
	active_player = player_a; reserve_player = player_b

func _create_player(pname: String) -> AudioStreamPlayer:
	var p = AudioStreamPlayer.new()
	p.name = pname; p.bus = "Ambience"; p.volume_db = mute_volume_db
	add_child(p); return p

func _ready() -> void:
	var bus = get_node_or_null("/root/EventBus")
	if bus:
		if bus.has_signal("zone_entered"): bus.zone_entered.connect(on_zone_entered)
		if bus.has_signal("player_moved"): bus.player_moved.connect(on_player_moved)

func register_zone(zid: String, stream: AudioStream) -> void:
	zone_streams[zid] = stream

func on_zone_entered(zid: String) -> void:
	if zid == current_zone_id: return
	if not zone_streams.has(zid) or zone_streams[zid] == null:
		push_warning("%s: Invalid or missing stream for zone '%s'" % [E_UNKNOWN_ZONE, zid])
		_fade_out_active(); current_zone_id = ""; return
	var stream: AudioStream = zone_streams[zid]
	_setup_loop(stream)
	if not active_player.playing:
		active_player.stream = stream; active_player.volume_db = default_volume_db
		active_player.play(); current_zone_id = zid; return
	var out_p = active_player; var in_p = reserve_player
	in_p.stream = stream; in_p.volume_db = mute_volume_db; in_p.play()
	if active_tween and active_tween.is_valid(): active_tween.kill()
	active_tween = create_tween().set_parallel(true)
	active_tween.tween_property(out_p, "volume_db", mute_volume_db, crossfade_time)
	active_tween.tween_property(in_p, "volume_db", default_volume_db, crossfade_time)
	active_tween.chain().tween_callback(func():
		out_p.stop(); out_p.volume_db = mute_volume_db
	)
	active_player = in_p; reserve_player = out_p; current_zone_id = zid

func _fade_out_active() -> void:
	if active_player and active_player.playing:
		if active_tween and active_tween.is_valid(): active_tween.kill()
		var p = active_player
		active_tween = create_tween()
		active_tween.tween_property(p, "volume_db", mute_volume_db, crossfade_time)
		active_tween.tween_callback(func(): p.stop(); p.volume_db = mute_volume_db)

func on_player_moved(pos: Vector3) -> void:
	listener_position = pos
	if not emitters_3d: return
	for child in emitters_3d.get_children():
		if child is Node3D:
			var d = child.global_position.distance_to(pos)
			if child.has_method("update_distance"): child.update_distance(d, max_distance_3d)
			elif child is AudioStreamPlayer3D: child.max_distance = max_distance_3d

func _setup_loop(stream: AudioStream) -> void:
	if stream is AudioStreamWAV and stream.loop_mode == AudioStreamWAV.LOOP_DISABLED:
		stream.loop_mode = AudioStreamWAV.LOOP_FORWARD
		stream.loop_end = stream.data.size() if stream.format == AudioStreamWAV.FORMAT_8_BITS else stream.data.size() / 2
	elif stream is AudioStreamOggVorbis: stream.loop = true

func seek(pos: float) -> void:
	if active_player and active_player.playing: active_player.seek(pos)

func get_playback_position() -> float:
	return active_player.get_playback_position() if active_player else 0.0

func zone_entered(zid: String) -> void: on_zone_entered(zid)
func player_moved(pos: Vector3) -> void: on_player_moved(pos)
