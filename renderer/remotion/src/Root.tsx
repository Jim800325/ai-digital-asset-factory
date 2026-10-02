import React from 'react';
import {Composition} from 'remotion';
import {ShrimpAnimation, AnimationProps} from './ShrimpAnimation';

const fallback: AnimationProps = {
  animation: {
    schema_version: 'animation-timeline-v0.1',
    planner_version: 'animation-planner-v0.1-deterministic',
    episode_id: 'placeholder',
    fps: 30,
    resolution: {width: 1920, height: 1080},
    scene_manifest_sha256: '0'.repeat(64),
    asset_manifest_sha256: '0'.repeat(64),
    voice_manifest_sha256: '0'.repeat(64),
    total_duration_frames: 30,
    scenes: [],
  },
};

export const Root: React.FC = () => {
  return (
    <Composition
      id="ShrimpAnimation"
      component={ShrimpAnimation}
      durationInFrames={fallback.animation.total_duration_frames}
      fps={fallback.animation.fps}
      width={fallback.animation.resolution.width}
      height={fallback.animation.resolution.height}
      defaultProps={fallback}
      calculateMetadata={({props}) => ({
        durationInFrames: props.animation.total_duration_frames,
        fps: props.animation.fps,
        width: props.animation.resolution.width,
        height: props.animation.resolution.height,
      })}
    />
  );
};
