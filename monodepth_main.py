# Modifications Srijan Parmeshwar 2017.
# Copybottom UCL Business plc 2017. Patent Pending. All bottoms reserved.
#
# The MonoDepth Software is licensed under the terms of the UCLB ACP-A licence
# which allows for non-commercial use only, the full terms of which are made
# available in the LICENSE file.
#
# For any other use of the software not covered by the UCLB ACP-A Licence, 
# please contact info@uclb.com

from __future__ import division

import argparse
import numpy as np
import os
import re
import tensorflow as tf
import tensorflow.contrib.slim as slim
import time

from monodepth_model import *
from monodepth_dataloader import *
from average_gradients import *

parser = argparse.ArgumentParser(description='Monodepth TensorFlow implementation.')

parser.add_argument('--mode',                      type=str,   help='Train or test', default='train')
parser.add_argument('--model_name',                type=str,   help='Model name', default='monodepth360')
parser.add_argument('--data_path',                 type=str,   help='Path to the data', required=True)
parser.add_argument('--filenames_file',            type=str,   help='Path to the filenames text file', required=True)
parser.add_argument('--input_height',              type=int,   help='Input height', default=256)
parser.add_argument('--input_width',               type=int,   help='Input width', default=512)
parser.add_argument('--batch_size',                type=int,   help='Batch size', default=8)
parser.add_argument('--num_epochs',                type=int,   help='Number of epochs', default=100)
parser.add_argument('--learning_rate',             type=float, help='Initial learning rate', default=1e-3)
parser.add_argument('--projection',                type=str,   help='Projection mode - cubic or equirectangular', default='cubic')
parser.add_argument('--tb_loss_weight',            type=float, help='Top-bottom consistency weight', default=1e-3)
parser.add_argument('--alpha_image_loss',          type=float, help='Weight between SSIM and L1 in the image loss', default=0.75)
parser.add_argument('--depth_gradient_loss_weight',type=float, help='Depth smoothness weight', default=1e-3)
parser.add_argument('--use_deconv',                            help='if set, will use transposed convolutions', action='store_true')
parser.add_argument('--gpus',                      type=str,   help='GPU indices to train on', default='0')
parser.add_argument('--num_threads',               type=int,   help='number of threads to use for data loading', default=8)
parser.add_argument('--output_directory',          type=str,   help='output directory for test disparities, if empty outputs to checkpoint folder', default='')
parser.add_argument('--log_directory',             type=str,   help='directory to save checkpoints and summaries', default='')
parser.add_argument('--checkpoint_path',           type=str,   help='path to a specific checkpoint to load', default='')
parser.add_argument('--retrain',                               help='if used with checkpoint_path, will restart training from step zero', action='store_true')
parser.add_argument('--full_summary',                          help='if set, will keep more data for each summary. Warning: the file can become very large', action='store_true')

args = parser.parse_args()

def setup_environment():
    # Only keep warnings and errors.
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '1'

    # Setup GPU usage.
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpus
    return len(args.gpus.split(","))

num_gpus = setup_environment()

def count_text_lines(file_path):
    with open(file_path, 'r') as f:
        lines = f.readlines()
        return len(lines)

def train(params):
    """Training loop."""

    with tf.Graph().as_default(), tf.device('/cpu:0'):

        global_step = tf.Variable(0, trainable=False)

        # OPTIMIZER
        num_training_samples = count_text_lines(args.filenames_file)
        
        steps_per_epoch = np.ceil(num_training_samples / params.batch_size).astype(np.int32)
        num_total_steps = params.num_epochs * steps_per_epoch

        boundaries = [np.int32((3/5) * num_total_steps), np.int32((4/5) * num_total_steps)]
        values = [args.learning_rate, args.learning_rate / 2, args.learning_rate / 4]
        learning_rate = tf.train.piecewise_constant(global_step, boundaries, values)
        
        opt_step = tf.train.AdamOptimizer(learning_rate)

        print("Total number of samples: {}".format(num_training_samples))
        print("Total number of steps: {}".format(num_total_steps))

        dataloader = MonodepthDataloader(args.data_path, args.filenames_file, params, args.mode)
        top  = dataloader.top_image_batch
        bottom = dataloader.bottom_image_batch

        # Split for each GPU.
        top_splits  = tf.split(top,  num_gpus, 0)
        bottom_splits = tf.split(bottom, num_gpus, 0)

        tower_grads  = []
        tower_losses = []
        reuse_variables = None
        with tf.variable_scope(tf.get_variable_scope()):
            for i in range(num_gpus):
                with tf.device('/gpu:%d' % i):

                    model = MonodepthModel(params, args.mode, top_splits[i], bottom_splits[i], reuse_variables, i)

                    loss = model.total_loss
                    tower_losses.append(loss)

                    reuse_variables = True

                    grads = opt_step.compute_gradients(loss)

                    tower_grads.append(grads)

        grads = average_gradients(tower_grads)

        apply_gradient_op = opt_step.apply_gradients(grads, global_step=global_step)

        total_loss = tf.reduce_mean(tower_losses)
        
        tf.summary.scalar('learning_rate', learning_rate, ['model_0'])
        tf.summary.scalar('total_loss', total_loss, ['model_0'])
        summary_op = tf.summary.merge_all('model_0')

        # SESSION
        config = tf.ConfigProto(allow_soft_placement=True)
        config.gpu_options.allow_growth=True
        session = tf.Session(config=config)

        # SAVER
        summary_writer = tf.summary.FileWriter(args.log_directory + '/' + args.model_name, session.graph)
        res_vars = slim.get_variables_to_restore(exclude = ["model/scaling"])
        train_saver = tf.train.Saver(res_vars)

        # COUNT PARAMS 
        total_num_parameters = 0
        for variable in tf.trainable_variables():
            total_num_parameters += np.array(variable.get_shape().as_list()).prod()
        print("Number of trainable parameters: {}".format(total_num_parameters))

        # INIT
        session.run(tf.global_variables_initializer())
        session.run(tf.local_variables_initializer())
        coordinator = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(sess=session, coord=coordinator)

        # LOAD CHECKPOINT IF SET
        if args.checkpoint_path != '':
            train_saver.restore(session, args.checkpoint_path)
            
            if args.retrain:
                session.run(global_step.assign(0))

        # GO!
        start_step = global_step.eval(session=session)
        start_time = time.time()
        for step in range(start_step, num_total_steps):
            before_op_time = time.time()
            _, loss_value = session.run([apply_gradient_op, total_loss])
            duration = time.time() - before_op_time
            if step and step % 100 == 0:
                examples_per_sec = params.batch_size / duration
                time_sofar = (time.time() - start_time) / 3600
                training_time_left = (num_total_steps / step - 1.0) * time_sofar
                print_string = 'Batch {:>6} | Examples/s: {:4.2f} | Loss: {:.5f} | Time elapsed: {:.2f}h | Time left: {:.2f}h'
                print(print_string.format(step, examples_per_sec, loss_value, time_sofar, training_time_left))
                summary_str = session.run(summary_op)
                summary_writer.add_summary(summary_str, global_step=step)
            if step and step % 10000 == 0:
                train_saver.save(session, args.log_directory + '/' + args.model_name + '/model', global_step=step)

        train_saver.save(session, args.log_directory + '/' + args.model_name + '/model', global_step=num_total_steps)

def test(params):
    """Test function."""

    dataloader = MonodepthDataloader(args.data_path, args.filenames_file, params, args.mode)
    top  = dataloader.top_image_batch
    bottom = dataloader.bottom_image_batch
    
    model = MonodepthModel(params, args.mode, top, bottom)

    # SESSION
    config = tf.ConfigProto(allow_soft_placement=True)
    session = tf.Session(config=config)

    # SAVER
    train_saver = tf.train.Saver()

    # INIT
    session.run(tf.global_variables_initializer())
    session.run(tf.local_variables_initializer())
    coordinator = tf.train.Coordinator()
    threads = tf.train.start_queue_runners(sess=session, coord=coordinator)

    # RESTORE
    if args.checkpoint_path == '':
        restore_path = tf.train.latest_checkpoint(args.log_directory + '/' + args.model_name)
    else:
        restore_path = args.checkpoint_path
    train_saver.restore(session, restore_path)

    num_test_samples = count_text_lines(args.filenames_file)

    print('now testing {} files'.format(num_test_samples))
    disparities    = np.zeros((num_test_samples, params.height, params.width), dtype=np.float32)
    for step in range(num_test_samples):
        disp = session.run(model.disparity_top_est[0])
        disparities[step] = disp[0].squeeze()

    print('done.')

    print('writing disparities.')
    if args.output_directory == '':
        output_directory = os.path.dirname(args.checkpoint_path)
    else:
        output_directory = args.output_directory
    np.save(output_directory + '/disparities.npy',    disparities)

    print('done.')

def main(_):

    params = monodepth_parameters(
        height=args.input_height,
        width=args.input_width,
        batch_size=args.batch_size,
        num_threads=args.num_threads,
        num_epochs=args.num_epochs,
        projection=args.projection,
        use_deconv=args.use_deconv,
        alpha_image_loss=args.alpha_image_loss, 
        depth_gradient_loss_weight=args.depth_gradient_loss_weight,
        tb_loss_weight=args.tb_loss_weight,
        full_summary=args.full_summary)

    if args.mode == 'train':
        train(params)
    elif args.mode == 'test':
        test(params)

if __name__ == '__main__':
    tf.app.run()