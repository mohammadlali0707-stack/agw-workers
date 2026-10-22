extends SceneTree

const AmbienceAudio = preload("ambience_audio.gd")
const MockZoneEmitter = preload("mocks/mock_zone_emitter.gd")

var ambience: AmbienceAudio
var phase: int = 0
var timer: float = 0.0
var s_loop: AudioStreamWAV
var s_b: AudioStreamWAV

func _make_stream(sec: float) -> AudioStreamWAV:
	var s = AudioStreamWAV.new()
	var d = PackedByteArray()
	var samples = int(44100 * sec)
	d.resize(samples)
	s.data = d
	s.format = AudioStreamWAV.FORMAT_8_BITS
	s.mix_rate = 44100
	s.loop_mode = AudioStreamWAV.LOOP_FORWARD
	s.loop_begin = 0
	s.loop_end = samples
	return s

func _process(delta: float) -> bool:
	timer += delta
	match phase:
		0:
			ambience = AmbienceAudio.new()
			root.add_child(ambience)
			s_loop = _make_stream(1.0)
			s_b = _make_stream(5.0)
			ambience.register_zone("A", s_loop)
			ambience.register_zone("B", s_b)
			ambience.zone_entered("A")
			ambience.seek(0.95)
			timer = 0.0
			phase = 1
		1:
			assert(ambience.active_player.playing, "Phase 1: active_player must remain playing during seek/loop")
			if timer >= 0.12:
				assert(ambience.get_playback_position() < 0.1, "Phase 1: playback_position must wrap around to start of loop")
				print("[PASS] Acceptance 1: Seamless loop test passed (pos=%f)" % ambience.get_playback_position())
				ambience.zone_entered("B")
				timer = 0.0
				phase = 2
		2:
			if timer >= 0.74 and timer <= 0.77:
				assert(ambience.player_a.playing and ambience.player_b.playing, "Phase 2: both players must be playing at t=0.75s")
				assert(ambience.player_a.volume_db < -10.0 and ambience.player_a.volume_db > -75.0, "Phase 2: player A volume must be intermediate at t=0.75s")
				assert(ambience.player_b.volume_db < -10.0 and ambience.player_b.volume_db > -75.0, "Phase 2: player B volume must be intermediate at t=0.75s")
				print("[PASS] Acceptance 2 (mid): Crossfade t=0.75s passed (A_vol=%f, B_vol=%f)" % [ambience.player_a.volume_db, ambience.player_b.volume_db])
			if timer >= 1.6:
				assert(not ambience.player_a.playing, "Phase 2: player A must be stopped at t=1.6s")
				assert(ambience.player_b.playing, "Phase 2: player B must be playing at t=1.6s")
				assert(abs(ambience.player_b.volume_db - (-6.0)) <= 0.1, "Phase 2: player B volume must be -6.0 dB +/- 0.1 at t=1.6s")
				print("[PASS] Acceptance 2 (end): Crossfade t=1.6s passed (A.playing=%s, B_vol=%f)" % [ambience.player_a.playing, ambience.player_b.volume_db])
				var pos_before = ambience.get_playback_position()
				var tw_before = ambience.active_tween
				ambience.zone_entered("B")
				assert(ambience.active_tween == tw_before, "Phase 3: re-entering same zone must not start new Tween")
				assert(ambience.get_playback_position() >= pos_before, "Phase 3: re-entering same zone must not reset position")
				print("[PASS] Acceptance 3: Repeat zone test passed")
				var emitter = MockZoneEmitter.new()
				emitter.position = Vector3(0, 0, 10)
				ambience.emitters_3d.add_child(emitter)
				ambience.player_moved(Vector3.ZERO)
				assert(emitter.is_in_range and abs(emitter.current_distance - 10.0) < 0.01, "Phase 4: 3D emitter distance calculation")
				ambience.player_moved(Vector3(0, 0, 40))
				assert(not emitter.is_in_range and emitter.volume_db == -80.0, "Phase 4: 3D emitter out-of-range attenuation")
				print("[PASS] 3D emitter distance and range calculations passed")
				ambience.zone_entered("UNKNOWN")
				timer = 0.0
				phase = 3
		3:
			if timer >= 1.6:
				assert(not ambience.player_b.playing and ambience.player_b.volume_db <= -79.9, "Phase 5: unknown zone must fade out active player to mute")
				print("[PASS] Unknown zone fade-out test passed")
				print("\n=== ALL AMBIENCE AUDIO HEADLESS TESTS PASSED SUCCESSFULLY ===")
				quit(0)
				return true
	return false
