# %% [markdown]
# # Plane Sweeping
#
# This function performs plane sweeping to estimate the depth of a pixel in a set of 2D
# images. The process for this was discussed in more detail in the workshop
# [7: Stereo Matching Fundamentals Continues](../workshops/07_stereo_matching_fundamentals_continued.ipynb).


# %%
from enum import Enum

import numpy as np
from nptyping import Float32, Int32, NDArray, Shape
from scipy.ndimage import map_coordinates
from scipy.signal import convolve2d

from oaf_vision_3d.lens_model import LensModel
from oaf_vision_3d.poly_2_subvalue_fit import find_subvalue_poly_2
from oaf_vision_3d.project_points import project_points
from oaf_vision_3d.transformation_matrix import TransformationMatrix


class CostFunction(Enum):
    SUM_OF_ABSOLUTE_DIFFERENCE = 0
    SUM_OF_SQUARED_DIFFERENCE = 1


def _get_cost(
    image_0: NDArray[Shape["H, W, ..."], Float32],
    images: list[NDArray[Shape["H, W, ..."], Float32]],
    cost_function: CostFunction,
) -> NDArray[Shape["H, W"], Float32]:
    match cost_function:
        case CostFunction.SUM_OF_ABSOLUTE_DIFFERENCE:
            return np.abs(image_0[None, ...] - np.array(images)).sum(axis=(0, -1))
        case CostFunction.SUM_OF_SQUARED_DIFFERENCE:
            return ((image_0[None, ...] - np.array(images)) ** 2).sum(axis=(0, -1))
        case _:
            raise ValueError("Invalid cost function")


def repeoject_image_at_depth(
    image: NDArray[Shape["H, W, ..."], Float32],
    camera_vectors: NDArray[Shape["H, W, 3"], Float32],
    depth: float,
    lens_model: LensModel,
    transformation_matrix: TransformationMatrix,
) -> NDArray[Shape["H, W, ..."], Float32]:
    xyz = camera_vectors * depth

    projected_points = project_points(
        points=xyz.reshape(-1, 3),
        lens_model=lens_model,
        transformation_matrix=transformation_matrix.inverse(),
    ).reshape(*camera_vectors.shape[:2], 2)

    return np.stack(
        [
            map_coordinates(
                input=_image,
                coordinates=[projected_points[..., 1], projected_points[..., 0]],
                order=1,
                mode="constant",
                cval=np.nan,
            )
            for _image in image.transpose(2, 0, 1)
        ],
        axis=-1,
        dtype=np.float32,
    )


def plane_sweeping(
    image: NDArray[Shape["H, W, ..."], Float32],
    lens_model: LensModel,
    secondary_images: list[NDArray[Shape["H, W, ..."], Float32]],
    secondary_lens_models: list[LensModel],
    secondary_transformation_matrices: list[TransformationMatrix],
    depth_range: NDArray[Shape["2"], Float32],
    step_size: float,
    block_size: NDArray[Shape["[x, y]"], Int32] = np.array([11, 11], dtype=np.int32),
    subpixel_fit: bool = True,
    cost_function: CostFunction = CostFunction.SUM_OF_ABSOLUTE_DIFFERENCE,
) -> NDArray[Shape["H, W, 3"], Float32]:
    pixels = np.indices(image.shape[:2], dtype=np.float32)[::-1].transpose((1, 2, 0))
    undistorted_normalized_pixels = lens_model.undistort_pixels(
        normalized_pixels=lens_model.normalize_pixels(pixels=pixels)
    )
    camera_vectors = np.pad(
        undistorted_normalized_pixels, ((0, 0), (0, 0), (0, 1)), constant_values=1.0
    )

    depths = np.arange(
        start=depth_range[0],
        stop=depth_range[1] + step_size,
        step=step_size,
        dtype=np.float32,
    )
    error = []
    for depth in depths:
        shifted_images = [
            repeoject_image_at_depth(
                image=_image,
                camera_vectors=camera_vectors,
                depth=depth,
                lens_model=_lens_model,
                transformation_matrix=_transformation_matrix,
            )
            for _image, _lens_model, _transformation_matrix in zip(
                secondary_images,
                secondary_lens_models,
                secondary_transformation_matrices,
            )
        ]
        single_pixel_error = _get_cost(
            image_0=image, images=shifted_images, cost_function=cost_function
        )

        convoluted_error = convolve2d(
            convolve2d(
                single_pixel_error,
                np.ones((1, block_size[0])) / block_size[0],
                mode="same",
            ),
            np.ones((block_size[1], 1)) / block_size[1],
            mode="same",
        )
        error.append(convoluted_error)
    error_array = np.array(error, dtype=np.float32)

    if subpixel_fit:
        output_value = find_subvalue_poly_2(values=depths, function_value=error_array)
    else:
        output_value = depths[np.argmin(error_array, axis=0)].astype(np.float32)

    output_value[output_value >= depths.max()] = np.nan
    output_value[output_value <= depths.min()] = np.nan

    return camera_vectors * output_value[..., None]
