import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Img,
  Sequence,
  interpolate,
  useCurrentFrame,
} from 'remotion';

type MediaRef = {
  logical_key: string;
  storage_uri: string;
  media_type: string;
  sha256: string;
};

type CameraCue = {
  type: string;
  start_frame: number;
  end_frame: number;
  from_scale: number;
  to_scale: number;
};

type CharacterCue = {
  character_id: string;
  asset: MediaRef;
  x: number;
  y: number;
  action: string;
  start_frame: number;
  end_frame: number;
};

type AudioCue = {
  voice_asset_id: string;
  line_id: string;
  speaker: string;
  media: MediaRef;
  start_frame: number;
  end_frame: number;
  measured_duration_ms: number;
};

type SubtitleCue = {
  line_id: string;
  speaker: string;
  text: string;
  start_frame: number;
  end_frame: number;
  safe_area_bottom: number;
  max_width: number;
};

type Scene = {
  scene_id: string;
  start_frame: number;
  end_frame: number;
  duration_frames: number;
  background: MediaRef;
  transition: string;
  camera: CameraCue[];
  characters: CharacterCue[];
  audio: AudioCue[];
  subtitles: SubtitleCue[];
};

export type AnimationProps = {
  animation: {
    schema_version: string;
    planner_version: string;
    episode_id: string;
    fps: number;
    resolution: {width: number; height: number};
    scene_manifest_sha256: string;
    asset_manifest_sha256: string;
    voice_manifest_sha256: string;
    total_duration_frames: number;
    scenes: Scene[];
  };
};

const cameraTransform = (
  frame: number,
  cues: CameraCue[],
): React.CSSProperties => {
  const cue = cues.find(
    (item) => frame >= item.start_frame && frame < item.end_frame,
  );
  if (!cue) {
    return {transform: 'scale(1)'};
  }
  const scale = interpolate(
    frame,
    [cue.start_frame, Math.max(cue.start_frame + 1, cue.end_frame - 1)],
    [cue.from_scale, cue.to_scale],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  let translateX = 0;
  if (cue.type === 'focus_left') translateX = 3;
  if (cue.type === 'focus_right') translateX = -3;
  if (cue.type === 'pan') {
    translateX = interpolate(
      frame,
      [cue.start_frame, Math.max(cue.start_frame + 1, cue.end_frame - 1)],
      [-2, 2],
      {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
    );
  }
  return {
    transform: `translateX(${translateX}%) scale(${scale})`,
    transformOrigin: 'center center',
  };
};

const SceneLayer: React.FC<{scene: Scene}> = ({scene}) => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill style={{overflow: 'hidden', backgroundColor: '#111'}}>
      <AbsoluteFill style={cameraTransform(frame, scene.camera)}>
        <Img
          src={scene.background.storage_uri}
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
          }}
        />
        {scene.characters.map((cue, index) => (
          <Sequence
            key={`${cue.character_id}-${cue.start_frame}-${index}`}
            from={cue.start_frame}
            durationInFrames={Math.max(1, cue.end_frame - cue.start_frame)}
            layout="none"
          >
            <Img
              src={cue.asset.storage_uri}
              style={{
                position: 'absolute',
                left: `${cue.x * 100}%`,
                top: `${cue.y * 100}%`,
                transform: 'translate(-50%, -100%)',
                maxHeight: '72%',
                maxWidth: '45%',
                objectFit: 'contain',
              }}
            />
          </Sequence>
        ))}
      </AbsoluteFill>

      {scene.audio.map((cue) => (
        <Sequence
          key={cue.voice_asset_id}
          from={cue.start_frame}
          durationInFrames={Math.max(1, cue.end_frame - cue.start_frame)}
          layout="none"
        >
          <Audio src={cue.media.storage_uri} />
        </Sequence>
      ))}

      {scene.subtitles.map((cue) => (
        <Sequence
          key={cue.line_id}
          from={cue.start_frame}
          durationInFrames={Math.max(1, cue.end_frame - cue.start_frame)}
          layout="none"
        >
          <div
            style={{
              position: 'absolute',
              left: '50%',
              bottom: `${cue.safe_area_bottom * 100}%`,
              transform: 'translateX(-50%)',
              maxWidth: `${cue.max_width * 100}%`,
              padding: '12px 20px',
              borderRadius: 12,
              background: 'rgba(0,0,0,0.72)',
              color: 'white',
              fontSize: 42,
              lineHeight: 1.35,
              textAlign: 'center',
              fontFamily: 'sans-serif',
            }}
          >
            {cue.text}
          </div>
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};

export const ShrimpAnimation: React.FC<AnimationProps> = ({animation}) => {
  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      {animation.scenes.map((scene) => (
        <Sequence
          key={scene.scene_id}
          from={scene.start_frame}
          durationInFrames={scene.duration_frames}
        >
          <SceneLayer scene={scene} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
