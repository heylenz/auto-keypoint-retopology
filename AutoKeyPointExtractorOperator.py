# ugly fix if openvino is
import os
from math import radians

from bpy_extras.view3d_utils import location_3d_to_region_2d

os.sys.path = list(filter(lambda x: "openvino" not in x, os.sys.path))

import bpy
from bpy_extras.object_utils import world_to_camera_view

import cv2
import dlib
import numpy as np
from imutils import face_utils
from scipy import spatial

# settings
DEBUG_MODE = False
LANDMARK_PATH = "/Users/cansik/git/zhdk/auto-keypoint-retopology/shape_predictor_68_face_landmarks.dat"
RENDER_DIR = "/Users/cansik/git/zhdk/auto-keypoint-retopology/"

# mapping
# todo: add mapping of keypoints

import blf


class Annotator:
    """Class used for adding 2d annotations at 3d positions"""

    def __init__(self, font_size=14):
        self.font_size = font_size
        self.font_id = 0
        self.font_handler = None
        self.font_handler_name = "annotator_font_handler"
        self.annotations = []

    def add_handler(self):
        """Adds annotation handler to 3d view"""
        font_handler = bpy.types.SpaceView3D.draw_handler_add(
            self.draw_callback_px, (None, None), 'WINDOW', 'POST_PIXEL')
        bpy.app.driver_namespace[self.font_handler_name] = font_handler

    def remove_handler(self):
        """Removes annoation handler from 3d view"""
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                bpy.app.driver_namespace[self.font_handler_name], 'WINDOW')
        except:
            print("no annotation event handler found")

    def add_annotation(self, position, text):
        """Adds a new annotation"""
        self.annotations.append((position, text))

    def clear_annotations(self):
        """Clears all annotations"""
        self.annotations.clear()

    def draw_callback_px(self, context, something):
        """Draw on the viewports"""
        rv3d = bpy.context.space_data.region_3d
        region = bpy.context.region

        for position, text in self.annotations:
            pos_text = location_3d_to_region_2d(region, rv3d, position)

            # todo: check if vertex is occluded
            blf.position(self.font_id, pos_text.x, pos_text.y, 0)
            blf.color(self.font_id, 1.0, 0.0, 1.0, 1.0)
            blf.size(self.font_id, self.font_size, 72)
            blf.draw(self.font_id, text)


class AutoKeyPointExtractorOperator(bpy.types.Operator):
    """Operator which runs its self from a timer"""
    bl_idname = "wm.auto_key_point_extractor_operator"
    bl_label = "Auto KeyPoint Extractor Operator"

    print(os.path.dirname(os.path.abspath(__file__)))
    detector = dlib.get_frontal_face_detector()
    predictor = dlib.shape_predictor(LANDMARK_PATH)
    annotator = Annotator()

    @staticmethod
    def render_to_file(filename):
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces[0].shading.type = 'RENDERED'

        bpy.context.scene.render.image_settings.file_format = 'PNG'
        bpy.context.scene.render.filepath = filename
        bpy.ops.render.render(use_viewport=True, write_still=True)

    @staticmethod
    def get_screen_coordinates(scene, cam, obj):
        mat = obj.matrix_world

        # Multiply matrix by vertex
        vertices = (mat @ vert.co for vert in obj.data.vertices)
        return [world_to_camera_view(scene, cam, coord) for coord in vertices]

    @staticmethod
    def scale_to_pixel(scene, screen_coordinates):
        render_scale = scene.render.resolution_percentage / 100
        render_size = (
            int(scene.render.resolution_x * render_scale),
            int(scene.render.resolution_y * render_scale),
        )

        # mapping coordinates
        return [list((round(v[0] * render_size[0]), render_size[1] - round(v[1] * render_size[1])))
                for v in screen_coordinates]

    @staticmethod
    def retreive_cam_oriented_matching_vertex(cam, tree, kp):
        # todo: filter points which are too fare away (otherwise backpoints match better)
        return tree.query(kp)

    def extract_keypoints(self, filename):
        image = cv2.imread(filename, cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # find land marks for first face
        rect = self.detector(gray, 0)[0]
        shape = self.predictor(gray, rect)
        shape = face_utils.shape_to_np(shape)

        # annotate keypoints in image
        i = 0
        for (x, y) in shape:
            cv2.putText(image, "%s" % i, (x + 2, y + 2), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 255, 255),
                        lineType=cv2.LINE_AA)
            cv2.circle(image, (x, y), 2, (0, 255, 255), -1)
            i += 1

        cv2.imwrite(RENDER_DIR + "/result.png", image)
        if DEBUG_MODE:
            cv2.imshow("Output", image)
            cv2.waitKey(1)
        return shape.tolist()

    def detect_vertices(self, scene, obj, cam, view_angle):
        # rotate object into detection-position
        rotation = obj.rotation_euler
        obj.rotation_euler = (rotation.x,
                              rotation.y,
                              rotation.z + radians(view_angle))

        # create render
        image_path = RENDER_DIR + "/render.png"
        self.render_to_file(image_path)

        # extract keypoints
        keypoints = self.extract_keypoints(image_path)

        # extract vertices
        screen_coordinates = self.get_screen_coordinates(scene, cam, obj)

        # create list but only take x and y as list (kp are 2d)
        screen_coordinate_list = [list(v[:2]) for v in screen_coordinates]
        scaled_screen_coordinates_list = self.scale_to_pixel(scene, screen_coordinate_list)

        if DEBUG_MODE:
            print("SCL: %s" % list(screen_coordinate_list[:5]))
            print("SSCL: %s" % list(scaled_screen_coordinates_list[:5]))

        tree = spatial.KDTree(scaled_screen_coordinates_list)

        # match screen coordinates to keypoint positions
        vertex_indexes = [self.retreive_cam_oriented_matching_vertex(cam, tree, kp) for kp in keypoints]

        # rotate object back into original position
        obj.rotation_euler = (rotation.x,
                              rotation.y,
                              rotation.z - radians(view_angle))

        return vertex_indexes

    def execute(self, context):
        self.annotator.remove_handler()

        # get object to be annotated
        if len(bpy.context.selected_objects) == 0:
            print("no object selected!")
            return {'FINISHED'}

        # read objects
        scene = bpy.context.scene
        obj = bpy.context.selected_objects[0]
        cam = bpy.data.objects['Camera']

        # run detection
        vertex_indexes = self.detect_vertices(scene, obj, cam, 0.0)
        mean_accuracy = np.mean(vertex_indexes, axis=0)

        # extract real vertices
        vertices = [obj.data.vertices[vi[1]].co for vi in vertex_indexes]
        world_vertices = list(obj.matrix_world @ vert for vert in vertices)

        # add annotation for each vertex
        self.annotator.clear_annotations()
        for i, v in enumerate(world_vertices):
            bpy.context.scene.cursor.location = (v.x, v.y, v.z)
            self.annotator.add_annotation(v, "x")
        self.annotator.add_handler()

        print("-----")
        print("Points Extracted: %s pts" % len(vertex_indexes))
        print("Mean Accuracy: %s px" % round(mean_accuracy[0], 4))
        print("-----")

        return {'FINISHED'}

    def cancel(self, context):
        cv2.destroyAllWindows()


def register():
    bpy.utils.register_class(AutoKeyPointExtractorOperator)


def unregister():
    bpy.utils.unregister_class(AutoKeyPointExtractorOperator)


if __name__ == "__main__":
    register()

    # test call
    bpy.ops.wm.auto_key_point_extractor_operator()
