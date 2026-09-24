"""Interactive perspective 3D arm drawn as shaded meshes on a Tk canvas."""
import math
import tkinter as tk
import numpy as np
from kinematics import MODELS, forward_kinematics, mounting_matrix, pose_matrix

class RobotView(tk.Canvas):
    def __init__(self, parent, model):
        super().__init__(parent, background="#101923", highlightthickness=0, width=700, height=560)
        self.model = model
        self.sample = None
        self.stream_state = "Live joint geometry"
        self.installation = None
        self.yaw, self.elevation, self.zoom = -.8, .45, 1.
        self.drag = None
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.orbit)
        self.bind("<MouseWheel>", lambda e: self.zoom_by(1.1 if e.delta > 0 else 1/1.1))
        self.bind("<Button-4>", lambda _: self.zoom_by(1.1))
        self.bind("<Button-5>", lambda _: self.zoom_by(1/1.1))

    def set_stream_state(self, state):
        if self.stream_state != state:
            self.stream_state = state
            self.redraw()

    def start_drag(self, event):
        self.drag = (event.x, event.y)

    def orbit(self, event):
        if self.drag:
            self.yaw -= (event.x - self.drag[0]) * .008
            self.elevation = float(np.clip(self.elevation + (event.y - self.drag[1]) * .008, -1.45, 1.45))
        self.drag = (event.x, event.y)
        self.redraw()

    def zoom_by(self, factor):
        self.zoom = float(np.clip(self.zoom * factor, .4, 3.))
        self.redraw()

    def reset_camera(self):
        self.yaw, self.elevation, self.zoom = -.8, .45, 1.
        self.redraw()

    def update_robot(self, sample, installation):
        self.sample, self.installation = sample, installation
        self.redraw()

    def project(self, points):
        points = np.asarray(points) - self.center
        depth = self.distance - points @ self.eye_direction
        scale = self.focal / np.maximum(depth, .05)
        xy = np.column_stack((self.w/2 + (points @ self.right)*scale,
                              self.h/2 - (points @ self.up)*scale))
        return xy, depth

    def line3(self, points, color, width=1, dash=None):
        xy, _ = self.project(points)
        self.create_line(*xy.ravel(), fill=color, width=width, dash=dash, tags="scene")

    def text3(self, point, text, color):
        xy, _ = self.project([point])
        self.create_text(*xy[0], text=text, fill=color, anchor="sw",
                         font=("TkDefaultFont", 10), tags="scene")

    def cylinder(self, start, end, radius, color):
        start, end = np.asarray(start), np.asarray(end)
        axis = end - start
        length = np.linalg.norm(axis)
        if length < 1e-8:
            return
        axis /= length
        helper = np.array([0., 0., 1.]) if abs(axis[2]) < .9 else np.array([1., 0., 0.])
        u = np.cross(axis, helper)
        u /= np.linalg.norm(u)
        v = np.cross(axis, u)
        angles = np.arange(12) * (2*math.pi/12)
        offsets = radius * (np.cos(angles)[:, None]*u + np.sin(angles)[:, None]*v)
        rings = (start + offsets, end + offsets)
        faces = [(rings[0][::-1], -axis), (rings[1], axis)]
        for i in range(12):
            j = (i + 1) % 12
            normal = offsets[i] + offsets[j]
            normal /= np.linalg.norm(normal)
            faces.append((np.array([rings[0][i], rings[0][j], rings[1][j], rings[1][i]]), normal))
        rgb = np.array(tuple(int(color[i:i+2], 16) for i in (1, 3, 5)))
        for vertices, normal in faces:
            brightness = .58 + .42 * max(0, np.dot(normal, self.light))
            shade = "#" + "".join(f"{int(c):02x}" for c in np.clip(rgb*brightness, 0, 255))
            xy, depth = self.project(vertices)
            self.faces.append((float(depth.mean()), xy, shade))

    def frame_axes(self, transform, size=.1):
        origin = transform[:3, 3]
        for i, color in enumerate(("#f07178", "#8cce8a", "#7eb8ff")):
            self.line3([origin, origin + size*transform[:3, i]], color, 2)

    def redraw(self):
        self.delete("scene")
        self.w, self.h = max(self.winfo_width(), 100), max(self.winfo_height(), 100)
        a2, a3, d1, d4, d5, d6 = MODELS[self.model]
        reach = abs(a2) + abs(a3) + d1 + d4 + d5 + d6
        self.center = np.array([0., 0., .15*reach])
        self.distance, self.focal = 2.7*reach, min(self.w, self.h)*1.7*self.zoom
        self.eye_direction = np.array([math.cos(self.elevation)*math.cos(self.yaw),
                                       math.cos(self.elevation)*math.sin(self.yaw), math.sin(self.elevation)])
        self.right = np.array([-math.sin(self.yaw), math.cos(self.yaw), 0])
        self.up = np.cross(self.eye_direction, self.right)
        self.light = np.array([.3, -.4, .866])
        for step in np.linspace(-reach, reach, 13):
            self.line3([(step, -reach, 0), (step, reach, 0)], "#233342")
            self.line3([(-reach, step, 0), (reach, step, 0)], "#233342")
        self.frame_axes(np.eye(4), .22*reach)
        if self.sample is None:
            self.create_text(self.w/2, self.h/2, text="Waiting for live joint positions…",
                             fill="#dce7f0", font=("TkDefaultFont", 16), tags="scene")
            return
        data = self.installation
        mount = mounting_matrix(*data.mounting) if data and data.mounting is not None else np.eye(4)
        frames = forward_kinematics(self.model, self.sample.joints, mount)
        points = np.array([f[:3, 3] for f in frames])
        self.faces = []
        size = min(.075, reach * .045)
        self.cylinder(points[0] - mount[:3, 2]*.035, points[0] + mount[:3, 2]*.045,
                      size*1.65, "#657b8c")
        for i in range(6):
            self.cylinder(points[i], points[i+1], size*(.85 if i < 3 else .65), "#c2d0d8")
            axis = frames[i][:3, 2]
            self.cylinder(points[i] - axis*size*.6, points[i] + axis*size*.6,
                          size*1.12, "#72b9dc")
        for _, xy, color in sorted(self.faces, key=lambda f: f[0], reverse=True):
            self.create_polygon(*xy.ravel(), fill=color, outline=color, tags="scene")
        live_tcp = mount @ pose_matrix(self.sample.tcp_pose)
        self.line3([points[-1], live_tcp[:3, 3]], "#63e2bb", 3)
        self.frame_axes(live_tcp, .08*reach)
        self.text3(live_tcp[:3, 3], "  Live TCP", "#63e2bb")
        if data and data.tcp is not None:
            saved_tcp = frames[-1] @ pose_matrix(data.tcp)
            self.line3([points[-1], saved_tcp[:3, 3]], "#c5a0ff", 2, (4, 3))
            self.text3(saved_tcp[:3, 3], "  Installation TCP", "#c5a0ff")
        if data and data.payload_mass is not None and data.payload_cog is not None and data.payload_mass > 0:
            cog = (frames[-1] @ np.array((*data.payload_cog, 1)))[:3]
            self.line3([points[-1], cog], "#ffc574", 2, (2, 3))
            xy, _ = self.project([cog])
            x, y = xy[0]
            self.create_oval(x-5, y-5, x+5, y+5, fill="#ffc574", outline="", tags="scene")
            self.text3(cog, f"  CoG · {data.payload_mass:g} kg", "#ffc574")
        self.create_text(18, 18, text=f"{self.model}   •   {self.stream_state}",
                         fill="#dce7f0", anchor="nw", font=("TkDefaultFont", 12, "bold"), tags="scene")
        self.create_text(18, self.h-18, text="Drag to orbit  ·  Scroll to zoom  ·  Grid and distances in metres",
                         fill="#9baec0", anchor="sw", tags="scene")
