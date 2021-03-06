# Modifications Srijan Parmeshwar 2017.
# Copyright UCL Business plc 2017. Patent Pending. All rights reserved.
#
# The MonoDepth Software is licensed under the terms of the UCLB ACP-A licence
# which allows for non-commercial use only, the full terms of which are made
# available in the LICENSE file.
#
# For any other use of the software not covered by the UCLB ACP-A Licence, 
# please contact info@uclb.com

"""Monodepth dataloader.
"""

import tensorflow as tf


def string_length_tf(t):
    return tf.py_func(len, [t], [tf.int64])


class MonodepthDataloader(object):
    """Monodepth dataloader"""

    def __init__(self, data_path, filenames_file, params, mode):
        self.data_path = data_path
        self.params = params
        self.mode = mode

        self.top_image_batch = None
        self.bottom_image_batch = None

        input_queue = tf.train.string_input_producer([filenames_file], shuffle=False)
        line_reader = tf.TextLineReader()
        _, line = line_reader.read(input_queue)

        split_line = tf.string_split([line]).values

        # We only load one image for testing.
        if mode == 'test':
            top_image_path = tf.string_join([self.data_path, '/top/', split_line[0], '.jpg'])
            top_image_o = self.read_image(top_image_path)
        else:
            top_image_path = tf.string_join([self.data_path, '/top/', split_line[0], '.jpg'])
            bottom_image_path = tf.string_join([self.data_path, '/bottom/', split_line[0], '.jpg'])
            top_image_o = self.read_image(top_image_path)
            bottom_image_o = self.read_image(bottom_image_path)

        if mode == 'train':
            # Randomly flip images.
            do_flip = tf.random_uniform([], 0, 1)
            top_image  = tf.cond(do_flip > 0.5, lambda: tf.image.flip_left_right(top_image_o), lambda: top_image_o)
            bottom_image = tf.cond(do_flip > 0.5, lambda: tf.image.flip_left_right(bottom_image_o),  lambda: bottom_image_o)

            # Randomly augment images.
            do_augment = tf.random_uniform([], 0, 1)
            top_image, bottom_image = tf.cond(do_augment > 0.5,
                                                        lambda: self.augment_image_pair(top_image,
                                                                                            bottom_image),
                                                        lambda: (top_image, bottom_image))

            top_image.set_shape([None, None, 3])
            bottom_image.set_shape([None, None, 3])

            # capacity = min_after_dequeue + (num_threads + a small safety margin) * batch_size
            min_after_dequeue = 2048
            capacity = min_after_dequeue + 4 * params.batch_size
            self.top_image_batch, self.bottom_image_batch = tf.train.shuffle_batch(
                [top_image, bottom_image],
                params.batch_size, capacity, min_after_dequeue, params.num_threads)

        elif mode == 'test':
            self.top_image_batch = tf.stack([top_image_o, tf.image.flip_left_right(top_image_o)], 0)
            self.top_image_batch.set_shape([2, None, None, 3])

    def augment_image_pair(self, top_image, bottom_image):
        # Randomly shift gamma.
        random_gamma = tf.random_uniform([], 0.8, 1.2)
        top_image_aug = top_image ** random_gamma
        bottom_image_aug = bottom_image ** random_gamma

        # Randomly shift brightness.
        random_brightness = tf.random_uniform([], 0.5, 2.0)
        top_image_aug = top_image_aug * random_brightness
        bottom_image_aug = bottom_image_aug * random_brightness

        # Randomly shift color.
        random_colors = tf.random_uniform([3], 0.8, 1.2)
        white = tf.ones([tf.shape(bottom_image)[0], tf.shape(bottom_image)[1]])
        color_image = tf.stack([white * random_colors[i] for i in range(3)], axis=2)
        top_image_aug *= color_image
        bottom_image_aug *= color_image

        # Saturate.
        top_image_aug = tf.clip_by_value(top_image_aug, 0, 1)
        bottom_image_aug = tf.clip_by_value(bottom_image_aug, 0, 1)

        return top_image_aug, bottom_image_aug

    def read_image(self, image_path):
        # tf.decode_image does not return the image size, this is an ugly workaround to handle both jpeg and png
        path_length = string_length_tf(image_path)[0]
        file_extension = tf.substr(image_path, path_length - 3, 3)
        file_cond = tf.equal(file_extension, 'jpg')

        image = tf.cond(file_cond, lambda: tf.image.decode_jpeg(tf.read_file(image_path)),
                        lambda: tf.image.decode_png(tf.read_file(image_path)))

        image = tf.image.convert_image_dtype(image, tf.float32)
        image = tf.image.resize_images(image, [self.params.height, self.params.width], tf.image.ResizeMethod.AREA)

        return image
