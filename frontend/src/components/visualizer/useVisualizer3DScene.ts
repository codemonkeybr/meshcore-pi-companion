import { useEffect, useRef, useState, type RefObject } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { CSS2DObject, CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js';

import { COLORS, getLinkId } from '../../utils/visualizerUtils';
import type { VisualizerData3D } from './useVisualizerData3D';
import {
  arraysEqual,
  getBaseNodeColor,
  getSceneNodeLabel,
  growFloat32Buffer,
  type NodeMeshData,
} from './shared';

interface UseVisualizer3DSceneArgs {
  containerRef: RefObject<HTMLDivElement | null>;
  data: VisualizerData3D;
  autoOrbit: boolean;
}

interface UseVisualizer3DSceneResult {
  hoveredNodeId: string | null;
  hoveredNeighborIds: string[];
  pinnedNodeId: string | null;
}

export function useVisualizer3DScene({
  containerRef,
  data,
  autoOrbit,
}: UseVisualizer3DSceneArgs): UseVisualizer3DSceneResult {
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cssRendererRef = useRef<CSS2DRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const nodeMeshesRef = useRef<Map<string, NodeMeshData>>(new Map());
  const raycastTargetsRef = useRef<THREE.Mesh[]>([]);
  const linkLineRef = useRef<THREE.LineSegments | null>(null);
  const dashedLinkLineRef = useRef<THREE.LineSegments | null>(null);
  const highlightLineRef = useRef<THREE.LineSegments | null>(null);
  const particlePointsRef = useRef<THREE.Points | null>(null);
  const particleTextureRef = useRef<THREE.Texture | null>(null);
  const linkPositionBufferRef = useRef<Float32Array>(new Float32Array(0));
  const dashedLinkPositionBufferRef = useRef<Float32Array>(new Float32Array(0));
  const highlightPositionBufferRef = useRef<Float32Array>(new Float32Array(0));
  const particlePositionBufferRef = useRef<Float32Array>(new Float32Array(0));
  const particleColorBufferRef = useRef<Float32Array>(new Float32Array(0));
  const raycasterRef = useRef(new THREE.Raycaster());
  const mouseRef = useRef(new THREE.Vector2());
  const dataRef = useRef(data);

  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const hoveredNodeIdRef = useRef<string | null>(null);
  const [hoveredNeighborIds, setHoveredNeighborIds] = useState<string[]>([]);
  const hoveredNeighborIdsRef = useRef<string[]>([]);
  const pinnedNodeIdRef = useRef<string | null>(null);
  const [pinnedNodeId, setPinnedNodeId] = useState<string | null>(null);

  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(COLORS.background);
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(60, 1, 1, 5000);
    camera.position.set(0, 0, 400);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    const texSize = 64;
    const texCanvas = document.createElement('canvas');
    texCanvas.width = texSize;
    texCanvas.height = texSize;
    const texCtx = texCanvas.getContext('2d');
    if (!texCtx) {
      renderer.dispose();
      if (renderer.domElement.parentNode) {
        renderer.domElement.parentNode.removeChild(renderer.domElement);
      }
      return;
    }
    const gradient = texCtx.createRadialGradient(
      texSize / 2,
      texSize / 2,
      0,
      texSize / 2,
      texSize / 2,
      texSize / 2
    );
    gradient.addColorStop(0, 'rgba(255,255,255,1)');
    gradient.addColorStop(0.5, 'rgba(255,255,255,0.8)');
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    texCtx.fillStyle = gradient;
    texCtx.fillRect(0, 0, texSize, texSize);
    const particleTexture = new THREE.CanvasTexture(texCanvas);
    particleTextureRef.current = particleTexture;

    const cssRenderer = new CSS2DRenderer();
    cssRenderer.domElement.style.position = 'absolute';
    cssRenderer.domElement.style.top = '0';
    cssRenderer.domElement.style.left = '0';
    cssRenderer.domElement.style.pointerEvents = 'none';
    cssRenderer.domElement.style.zIndex = '1';
    container.appendChild(cssRenderer.domElement);
    cssRendererRef.current = cssRenderer;

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    controls.minDistance = 50;
    controls.maxDistance = 2000;
    controlsRef.current = controls;

    const linkGeometry = new THREE.BufferGeometry();
    const linkMaterial = new THREE.LineBasicMaterial({
      color: COLORS.link,
      transparent: true,
      opacity: 0.6,
    });
    const linkSegments = new THREE.LineSegments(linkGeometry, linkMaterial);
    linkSegments.visible = false;
    scene.add(linkSegments);
    linkLineRef.current = linkSegments;

    const dashedLinkGeometry = new THREE.BufferGeometry();
    const dashedLinkMaterial = new THREE.LineDashedMaterial({
      color: 0x94a3b8,
      transparent: true,
      opacity: 0.85,
      dashSize: 16,
      gapSize: 10,
    });
    const dashedLinkSegments = new THREE.LineSegments(dashedLinkGeometry, dashedLinkMaterial);
    dashedLinkSegments.visible = false;
    scene.add(dashedLinkSegments);
    dashedLinkLineRef.current = dashedLinkSegments;

    const highlightGeometry = new THREE.BufferGeometry();
    const highlightMaterial = new THREE.LineBasicMaterial({
      color: 0xffd700,
      transparent: true,
      opacity: 1,
      linewidth: 2,
    });
    const highlightSegments = new THREE.LineSegments(highlightGeometry, highlightMaterial);
    highlightSegments.visible = false;
    scene.add(highlightSegments);
    highlightLineRef.current = highlightSegments;

    const particleGeometry = new THREE.BufferGeometry();
    const particleMaterial = new THREE.PointsMaterial({
      size: 20,
      map: particleTexture,
      vertexColors: true,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
    });
    const particlePoints = new THREE.Points(particleGeometry, particleMaterial);
    particlePoints.visible = false;
    scene.add(particlePoints);
    particlePointsRef.current = particlePoints;

    const rect = container.getBoundingClientRect();
    renderer.setSize(rect.width, rect.height);
    cssRenderer.setSize(rect.width, rect.height);
    camera.aspect = rect.width / rect.height;
    camera.updateProjectionMatrix();

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width === 0 || height === 0) continue;
        renderer.setSize(width, height);
        cssRenderer.setSize(width, height);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
      }
    });
    observer.observe(container);

    const nodeMeshes = nodeMeshesRef.current;
    return () => {
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode) {
        renderer.domElement.parentNode.removeChild(renderer.domElement);
      }
      if (cssRenderer.domElement.parentNode) {
        cssRenderer.domElement.parentNode.removeChild(cssRenderer.domElement);
      }
      for (const nd of nodeMeshes.values()) {
        nd.mesh.remove(nd.label);
        nd.labelDiv.remove();
        scene.remove(nd.mesh);
        nd.mesh.geometry.dispose();
        (nd.mesh.material as THREE.Material).dispose();
      }
      nodeMeshes.clear();
      raycastTargetsRef.current = [];

      if (linkLineRef.current) {
        scene.remove(linkLineRef.current);
        linkLineRef.current.geometry.dispose();
        (linkLineRef.current.material as THREE.Material).dispose();
        linkLineRef.current = null;
      }
      if (dashedLinkLineRef.current) {
        scene.remove(dashedLinkLineRef.current);
        dashedLinkLineRef.current.geometry.dispose();
        (dashedLinkLineRef.current.material as THREE.Material).dispose();
        dashedLinkLineRef.current = null;
      }
      if (highlightLineRef.current) {
        scene.remove(highlightLineRef.current);
        highlightLineRef.current.geometry.dispose();
        (highlightLineRef.current.material as THREE.Material).dispose();
        highlightLineRef.current = null;
      }
      if (particlePointsRef.current) {
        scene.remove(particlePointsRef.current);
        particlePointsRef.current.geometry.dispose();
        (particlePointsRef.current.material as THREE.Material).dispose();
        particlePointsRef.current = null;
      }
      particleTexture.dispose();
      particleTextureRef.current = null;
      linkPositionBufferRef.current = new Float32Array(0);
      dashedLinkPositionBufferRef.current = new Float32Array(0);
      highlightPositionBufferRef.current = new Float32Array(0);
      particlePositionBufferRef.current = new Float32Array(0);
      particleColorBufferRef.current = new Float32Array(0);
      sceneRef.current = null;
      cameraRef.current = null;
      rendererRef.current = null;
      cssRendererRef.current = null;
      controlsRef.current = null;
    };
  }, [containerRef]);

  useEffect(() => {
    const controls = controlsRef.current;
    if (!controls) return;
    controls.autoRotate = autoOrbit;
    controls.autoRotateSpeed = -0.5;
  }, [autoOrbit]);

  useEffect(() => {
    const renderer = rendererRef.current;
    const camera = cameraRef.current;
    if (!renderer || !camera) return;

    const onMouseMove = (event: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      mouseRef.current.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouseRef.current.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    };

    let mouseDownPos = { x: 0, y: 0 };

    const onMouseDown = (event: MouseEvent) => {
      mouseDownPos = { x: event.clientX, y: event.clientY };
    };

    const onMouseUp = (event: MouseEvent) => {
      const dx = event.clientX - mouseDownPos.x;
      const dy = event.clientY - mouseDownPos.y;
      if (dx * dx + dy * dy > 25) return;

      const rect = renderer.domElement.getBoundingClientRect();
      const clickMouse = new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1
      );

      const raycaster = raycasterRef.current;
      raycaster.setFromCamera(clickMouse, camera);
      const intersects = raycaster.intersectObjects(raycastTargetsRef.current, false);
      const clickedObject = intersects[0]?.object as THREE.Mesh | undefined;
      const clickedId = (clickedObject?.userData?.nodeId as string | undefined) ?? null;

      if (clickedId === pinnedNodeIdRef.current) {
        pinnedNodeIdRef.current = null;
        setPinnedNodeId(null);
      } else if (clickedId) {
        pinnedNodeIdRef.current = clickedId;
        setPinnedNodeId(clickedId);
      } else {
        pinnedNodeIdRef.current = null;
        setPinnedNodeId(null);
      }
    };

    renderer.domElement.addEventListener('mousemove', onMouseMove);
    renderer.domElement.addEventListener('mousedown', onMouseDown);
    renderer.domElement.addEventListener('mouseup', onMouseUp);
    return () => {
      renderer.domElement.removeEventListener('mousemove', onMouseMove);
      renderer.domElement.removeEventListener('mousedown', onMouseDown);
      renderer.domElement.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  useEffect(() => {
    const scene = sceneRef.current;
    const camera = cameraRef.current;
    const renderer = rendererRef.current;
    const cssRenderer = cssRendererRef.current;
    const controls = controlsRef.current;
    if (!scene || !camera || !renderer || !cssRenderer || !controls) return;

    let running = true;

    const animate = () => {
      if (!running) return;
      requestAnimationFrame(animate);

      controls.update();

      const { nodes, links, particles } = dataRef.current;
      const currentNodeIds = new Set<string>();

      for (const node of nodes.values()) {
        currentNodeIds.add(node.id);

        let nd = nodeMeshesRef.current.get(node.id);
        if (!nd) {
          const isSelf = node.type === 'self';
          const radius = isSelf ? 12 : 6;
          const geometry = new THREE.SphereGeometry(radius, 16, 12);
          const material = new THREE.MeshBasicMaterial({ color: getBaseNodeColor(node) });
          const mesh = new THREE.Mesh(geometry, material);
          mesh.userData.nodeId = node.id;
          scene.add(mesh);

          const labelDiv = document.createElement('div');
          labelDiv.style.color = node.isAmbiguous ? COLORS.ambiguous : '#e5e7eb';
          labelDiv.style.fontSize = '11px';
          labelDiv.style.fontFamily = 'sans-serif';
          labelDiv.style.textAlign = 'center';
          labelDiv.style.whiteSpace = 'nowrap';
          labelDiv.style.textShadow = '0 0 4px #000, 0 0 2px #000';
          const label = new CSS2DObject(labelDiv);
          label.position.set(0, -(radius + 6), 0);
          mesh.add(label);

          nd = { mesh, label, labelDiv };
          nodeMeshesRef.current.set(node.id, nd);
          raycastTargetsRef.current.push(mesh);
        }

        nd.mesh.position.set(node.x ?? 0, node.y ?? 0, node.z ?? 0);
        const labelColor = node.isAmbiguous ? COLORS.ambiguous : '#e5e7eb';
        if (nd.labelDiv.style.color !== labelColor) {
          nd.labelDiv.style.color = labelColor;
        }
        const labelText = getSceneNodeLabel(node);
        if (nd.labelDiv.textContent !== labelText) {
          nd.labelDiv.textContent = labelText;
        }
      }

      for (const [id, nd] of nodeMeshesRef.current) {
        if (!currentNodeIds.has(id)) {
          nd.mesh.remove(nd.label);
          nd.labelDiv.remove();
          scene.remove(nd.mesh);
          nd.mesh.geometry.dispose();
          (nd.mesh.material as THREE.Material).dispose();
          const meshIdx = raycastTargetsRef.current.indexOf(nd.mesh);
          if (meshIdx >= 0) raycastTargetsRef.current.splice(meshIdx, 1);
          nodeMeshesRef.current.delete(id);
        }
      }

      raycasterRef.current.setFromCamera(mouseRef.current, camera);
      const intersects = raycasterRef.current.intersectObjects(raycastTargetsRef.current, false);
      const hitObject = intersects[0]?.object as THREE.Mesh | undefined;
      const hitId = (hitObject?.userData?.nodeId as string | undefined) ?? null;
      if (hitId !== hoveredNodeIdRef.current) {
        hoveredNodeIdRef.current = hitId;
        setHoveredNodeId(hitId);
      }
      const activeId = pinnedNodeIdRef.current ?? hoveredNodeIdRef.current;

      const solidLinks = [];
      const dashedLinks = [];
      for (const link of links.values()) {
        const { sourceId, targetId } = getLinkId(link);
        if (currentNodeIds.has(sourceId) && currentNodeIds.has(targetId)) {
          if (link.hasDirectObservation || !link.hasHiddenIntermediate) {
            solidLinks.push(link);
          } else {
            dashedLinks.push(link);
          }
        }
      }

      const connectedIds = activeId ? new Set<string>([activeId]) : null;

      const linkLine = linkLineRef.current;
      if (linkLine) {
        const geometry = linkLine.geometry as THREE.BufferGeometry;
        const requiredLength = solidLinks.length * 6;
        const highlightRequiredLength = (solidLinks.length + dashedLinks.length) * 6;
        if (linkPositionBufferRef.current.length < requiredLength) {
          linkPositionBufferRef.current = growFloat32Buffer(
            linkPositionBufferRef.current,
            requiredLength
          );
          geometry.setAttribute(
            'position',
            new THREE.BufferAttribute(linkPositionBufferRef.current, 3).setUsage(
              THREE.DynamicDrawUsage
            )
          );
        }

        const highlightLine = highlightLineRef.current;
        if (highlightLine && highlightPositionBufferRef.current.length < highlightRequiredLength) {
          highlightPositionBufferRef.current = growFloat32Buffer(
            highlightPositionBufferRef.current,
            highlightRequiredLength
          );
          (highlightLine.geometry as THREE.BufferGeometry).setAttribute(
            'position',
            new THREE.BufferAttribute(highlightPositionBufferRef.current, 3).setUsage(
              THREE.DynamicDrawUsage
            )
          );
        }

        const positions = linkPositionBufferRef.current;
        const hlPositions = highlightPositionBufferRef.current;
        let idx = 0;
        let hlIdx = 0;

        for (const link of solidLinks) {
          const { sourceId, targetId } = getLinkId(link);
          const sNode = nodes.get(sourceId);
          const tNode = nodes.get(targetId);
          if (!sNode || !tNode) continue;

          const sx = sNode.x ?? 0;
          const sy = sNode.y ?? 0;
          const sz = sNode.z ?? 0;
          const tx = tNode.x ?? 0;
          const ty = tNode.y ?? 0;
          const tz = tNode.z ?? 0;

          positions[idx++] = sx;
          positions[idx++] = sy;
          positions[idx++] = sz;
          positions[idx++] = tx;
          positions[idx++] = ty;
          positions[idx++] = tz;

          if (activeId && (sourceId === activeId || targetId === activeId)) {
            connectedIds?.add(sourceId === activeId ? targetId : sourceId);
            hlPositions[hlIdx++] = sx;
            hlPositions[hlIdx++] = sy;
            hlPositions[hlIdx++] = sz;
            hlPositions[hlIdx++] = tx;
            hlPositions[hlIdx++] = ty;
            hlPositions[hlIdx++] = tz;
          }
        }

        for (const link of dashedLinks) {
          const { sourceId, targetId } = getLinkId(link);
          if (activeId && (sourceId === activeId || targetId === activeId)) {
            const sNode = nodes.get(sourceId);
            const tNode = nodes.get(targetId);
            if (!sNode || !tNode) continue;

            connectedIds?.add(sourceId === activeId ? targetId : sourceId);
            hlPositions[hlIdx++] = sNode.x ?? 0;
            hlPositions[hlIdx++] = sNode.y ?? 0;
            hlPositions[hlIdx++] = sNode.z ?? 0;
            hlPositions[hlIdx++] = tNode.x ?? 0;
            hlPositions[hlIdx++] = tNode.y ?? 0;
            hlPositions[hlIdx++] = tNode.z ?? 0;
          }
        }

        const positionAttr = geometry.getAttribute('position') as THREE.BufferAttribute | undefined;
        if (positionAttr) {
          positionAttr.needsUpdate = true;
        }
        geometry.setDrawRange(0, idx / 3);
        linkLine.visible = idx > 0;

        if (highlightLine) {
          const hlGeometry = highlightLine.geometry as THREE.BufferGeometry;
          const hlAttr = hlGeometry.getAttribute('position') as THREE.BufferAttribute | undefined;
          if (hlAttr) {
            hlAttr.needsUpdate = true;
          }
          hlGeometry.setDrawRange(0, hlIdx / 3);
          highlightLine.visible = hlIdx > 0;
        }
      }

      const dashedLinkLine = dashedLinkLineRef.current;
      if (dashedLinkLine) {
        const geometry = dashedLinkLine.geometry as THREE.BufferGeometry;
        const requiredLength = dashedLinks.length * 6;
        if (dashedLinkPositionBufferRef.current.length < requiredLength) {
          dashedLinkPositionBufferRef.current = growFloat32Buffer(
            dashedLinkPositionBufferRef.current,
            requiredLength
          );
          geometry.setAttribute(
            'position',
            new THREE.BufferAttribute(dashedLinkPositionBufferRef.current, 3).setUsage(
              THREE.DynamicDrawUsage
            )
          );
        }

        const positions = dashedLinkPositionBufferRef.current;
        let idx = 0;

        for (const link of dashedLinks) {
          const { sourceId, targetId } = getLinkId(link);
          const sNode = nodes.get(sourceId);
          const tNode = nodes.get(targetId);
          if (!sNode || !tNode) continue;

          positions[idx++] = sNode.x ?? 0;
          positions[idx++] = sNode.y ?? 0;
          positions[idx++] = sNode.z ?? 0;
          positions[idx++] = tNode.x ?? 0;
          positions[idx++] = tNode.y ?? 0;
          positions[idx++] = tNode.z ?? 0;
        }

        const positionAttr = geometry.getAttribute('position') as THREE.BufferAttribute | undefined;
        if (positionAttr) {
          positionAttr.needsUpdate = true;
        }
        geometry.setDrawRange(0, idx / 3);
        dashedLinkLine.visible = idx > 0;
        if (idx > 0 && positionAttr) {
          dashedLinkLine.computeLineDistances();
        }
      }

      let writeIdx = 0;
      for (let readIdx = 0; readIdx < particles.length; readIdx++) {
        const particle = particles[readIdx];
        particle.progress += particle.speed;
        if (particle.progress <= 1) {
          particles[writeIdx++] = particle;
        }
      }
      particles.length = writeIdx;

      const particlePoints = particlePointsRef.current;
      if (particlePoints) {
        const geometry = particlePoints.geometry as THREE.BufferGeometry;
        const requiredLength = particles.length * 3;

        if (particlePositionBufferRef.current.length < requiredLength) {
          particlePositionBufferRef.current = growFloat32Buffer(
            particlePositionBufferRef.current,
            requiredLength
          );
          geometry.setAttribute(
            'position',
            new THREE.BufferAttribute(particlePositionBufferRef.current, 3).setUsage(
              THREE.DynamicDrawUsage
            )
          );
        }
        if (particleColorBufferRef.current.length < requiredLength) {
          particleColorBufferRef.current = growFloat32Buffer(
            particleColorBufferRef.current,
            requiredLength
          );
          geometry.setAttribute(
            'color',
            new THREE.BufferAttribute(particleColorBufferRef.current, 3).setUsage(
              THREE.DynamicDrawUsage
            )
          );
        }

        const pPositions = particlePositionBufferRef.current;
        const pColors = particleColorBufferRef.current;
        const color = new THREE.Color();
        let visibleCount = 0;

        for (const p of particles) {
          if (p.progress < 0) continue;
          if (!currentNodeIds.has(p.fromNodeId) || !currentNodeIds.has(p.toNodeId)) continue;

          const fromNode = nodes.get(p.fromNodeId);
          const toNode = nodes.get(p.toNodeId);
          if (!fromNode || !toNode) continue;

          const t = p.progress;
          const x = (fromNode.x ?? 0) + ((toNode.x ?? 0) - (fromNode.x ?? 0)) * t;
          const y = (fromNode.y ?? 0) + ((toNode.y ?? 0) - (fromNode.y ?? 0)) * t;
          const z = (fromNode.z ?? 0) + ((toNode.z ?? 0) - (fromNode.z ?? 0)) * t;

          pPositions[visibleCount * 3] = x;
          pPositions[visibleCount * 3 + 1] = y;
          pPositions[visibleCount * 3 + 2] = z;

          color.set(p.color);
          pColors[visibleCount * 3] = color.r;
          pColors[visibleCount * 3 + 1] = color.g;
          pColors[visibleCount * 3 + 2] = color.b;
          visibleCount++;
        }

        const posAttr = geometry.getAttribute('position') as THREE.BufferAttribute | undefined;
        const colorAttr = geometry.getAttribute('color') as THREE.BufferAttribute | undefined;
        if (posAttr) posAttr.needsUpdate = true;
        if (colorAttr) colorAttr.needsUpdate = true;
        geometry.setDrawRange(0, visibleCount);
        particlePoints.visible = visibleCount > 0;
      }

      const nextNeighbors = connectedIds
        ? Array.from(connectedIds)
            .filter((id) => id !== activeId)
            .sort()
        : [];
      if (!arraysEqual(hoveredNeighborIdsRef.current, nextNeighbors)) {
        hoveredNeighborIdsRef.current = nextNeighbors;
        setHoveredNeighborIds(nextNeighbors);
      }

      for (const [id, nd] of nodeMeshesRef.current) {
        const node = nodes.get(id);
        if (!node) continue;
        const mat = nd.mesh.material as THREE.MeshBasicMaterial;
        if (id === activeId) {
          mat.color.set(0xffd700);
        } else if (connectedIds?.has(id)) {
          mat.color.set(0xfff0b3);
        } else {
          mat.color.set(getBaseNodeColor(node));
        }
      }

      renderer.render(scene, camera);
      cssRenderer.render(scene, camera);
    };

    animate();
    return () => {
      running = false;
    };
  }, []);

  return { hoveredNodeId, hoveredNeighborIds, pinnedNodeId };
}
