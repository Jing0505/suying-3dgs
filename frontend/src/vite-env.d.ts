/// <reference types="vite/client" />

declare module "*.json" {
  const value: Record<string, unknown>;
  export default value;
}

declare module "@mkkellogg/gaussian-splats-3d" {
  export interface ViewerOptions {
    rootElement?: HTMLElement | null;
    cameraUp?: [number, number, number];
    initialCameraPosition?: [number, number, number];
    initialCameraLookAt?: [number, number, number];
    sharedMemoryForWorkers?: boolean;
    gpuAcceleratedSort?: boolean;
    halfPrecisionCovariancesOnGPU?: boolean;
    integerBasedSort?: boolean;
    useBuiltInControls?: boolean;
    selfDrivenMode?: boolean;
    ignoreDevicePixelRatio?: boolean;
    antialiased?: boolean;
    dynamicScene?: boolean;
  }

  export interface SplatSceneOptions {
    path: string;
    format?: number;
    splatAlphaRemovalThreshold?: number;
    position?: [number, number, number];
    rotation?: [number, number, number, number];
    scale?: [number, number, number];
  }

  export class Viewer {
    constructor(options?: ViewerOptions);
    addSplatScene(path: string, options?: Record<string, unknown>): Promise<void>;
    addSplatScenes(
      sceneOptions: SplatSceneOptions[],
      showLoadingUI?: boolean,
      onProgress?: (progress: number, stage: string) => void
    ): Promise<void>;
    start(): void;
    dispose(): Promise<void>;
  }

  export const SceneFormat: { Ply: number; Splat: number; KSplat: number };
  export const RenderMode: { [key: string]: number };
  export const SplatRenderMode: { [key: string]: number };
  export const SceneRevealMode: { [key: string]: number };
}
