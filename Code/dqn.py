from random import random

import gym
import numpy as np
import tensorflow as tf
from gym import wrappers

import run_dqn_SingleDevice


class GymDQNLearner:
    def __init__(self):
        self.saving_path = './saved_models/dqn/'
        self.epochs = 10000
        self.gamma = .9
        self.epsilon = 1.
        self.train_per_epoch = 1
        self.n_generating_trajectories_per_epoch = 1
        self.max_memory_size = 2000
        self.max_trajectory_length = 1000
        self.batch_size = 256

        # self.env = gym.make('CartPole-v0').env
        # self.state_embedding_size = self.env.observation_space.shape[0]
        # self.number_of_actions = self.env.action_space.n
        self.env = single_device_env.get_random_env()
        self.state_embedding_size = self.env.get_obs_shape()[0]
        self.number_of_actions = self.env.get_action_shape()
        print(self.state_embedding_size, self.number_of_actions)
        self.layer_units = [32, 16, self.number_of_actions]
        # self.layer_units = [64, 32, self.number_of_actions]
        self.layer_activations = ['tanh', 'relu', None]
        # self.layer_keep_probs = [.1, .1, 1.]
        # self.layer_regularizers = [tf.contrib.layers.l2_regularizer(1.),
        #                            tf.contrib.layers.l2_regularizer(1.),
        #                            tf.contrib.layers.l2_regularizer(1.)]
        self.layer_keep_probs = [1., 1., 1.]
        self.layer_regularizers = [None,
                                   None,
                                   None]
        self.initialize_experience_replay_memory()

        self.create_model()
        self.load()

    def initialize_experience_replay_memory(self):
        self.experience_replay_memory = np.array([])

    def get_epsilon(self, i):
        # alpha = 1e-5
        # return 1.0 - (i / np.sqrt(1 + alpha * (i ** 2))) * np.sqrt(alpha)
        # return 1.0 - float(i) / epochs
        return max(0.1, self.epsilon * (0.9989 ** i))
        # return 1

    def get_state_weights(self, trajectory):
        # total_reward = len(trajectory)
        total_reward = np.sum([t[2] for t in trajectory])
        # cum_reward = np.cumsum([t[2] for t in trajectory])
        return [total_reward for i, t in enumerate(trajectory)]

    def add_to_memory(self, trajectory):
        weights = self.get_state_weights(trajectory)
        for (from_state, action, reward, to_state, done, q_value), weight in zip(trajectory, weights):
            if self.experience_replay_memory.shape[0] >= self.max_memory_size:
                # self.experience_replay_memory = \
                #     np.delete(self.experience_replay_memory, np.random.randint(0, self.experience_replay_memory.shape[0]))
                # self.experience_replay_memory = self.experience_replay_memory[1:]
                min_element = np.argmin([exp['weight'] for exp in self.experience_replay_memory])
                self.experience_replay_memory = \
                    np.delete(self.experience_replay_memory, min_element)
            self.experience_replay_memory = np.append(self.experience_replay_memory, [
                {'from': from_state, 'action': action,
                 'reward': reward, 'done': done,
                 'to': to_state,
                 'q_value': q_value,
                 'weight': weight}])

    def softmax(self, logits):
        exps = np.exp(logits)
        return exps / np.sum(exps)

    def sample_from_memory(self):
        if self.experience_replay_memory.shape[0] > 1:
            weights = np.array([exp['weight'] for exp in self.experience_replay_memory])
            # p = weights / np.sum(weights)
            p = self.softmax(weights)
            return np.random.choice(self.experience_replay_memory,
                                    np.min([self.batch_size, self.experience_replay_memory.shape[0]]), p=p)
        else:
            return self.experience_replay_memory

    def create_multilayer_dense(self, scope, layer_input, layer_units, layer_activations, keep_probs=None,
                                regularizers=None, reuse_vars=None):
        with tf.variable_scope(scope, reuse=reuse_vars):
            last_layer = None
            if regularizers is None:
                regularizers = [None for _ in layer_units]
            if keep_probs is None:
                keep_probs = [1. for _ in layer_units]
            for i, (layer_size, activation, keep_prob, reg) in enumerate(zip(layer_units, layer_activations,
                                                                             keep_probs, regularizers)):
                if i == 0:
                    inp = layer_input
                else:
                    inp = last_layer
                last_layer = tf.layers.dense(inp, layer_size, activation, activity_regularizer=reg)
                if keep_prob != 1.0:
                    last_layer = tf.nn.dropout(last_layer, keep_prob)
        return last_layer

    def create_model(self):
        self.inputs = tf.placeholder(np.float32, [None, self.state_embedding_size], name='inputs')
        self.outputs = tf.placeholder(np.float32, [None, self.number_of_actions], name='outputs')

        self.output_layer = \
            self.create_multilayer_dense('q_func', self.inputs, self.layer_units, self.layer_activations,
                                         self.layer_keep_probs, self.layer_regularizers)
        self.test_output_layer = self.create_multilayer_dense('q_func', self.inputs, self.layer_units,
                                                              self.layer_activations, reuse_vars=True)
        self.loss = tf.losses.mean_squared_error(self.outputs, self.output_layer, scope='q_func')

        trainable_variables = tf.trainable_variables('q_func')
        self.train_op = tf.train.AdamOptimizer(1e-3, name='optimizer').minimize(self.loss, var_list=trainable_variables)
        self.saver = tf.train.Saver()
        self.sess = tf.Session()

    def get_action(self, epoch, q_value):
        if random() < self.get_epsilon(epoch):
            # action = self.env.action_space.sample()
            action = self.env.action_space_sample()
        else:
            action = np.argmax(q_value)
        return action

    def generate_new_trajectories(self, epoch):
        for _ in range(self.n_generating_trajectories_per_epoch):
            observation = self.env.reset()
            done = False
            trajectory = []
            while not done:
                q_value = self.sess.run(self.test_output_layer, {self.inputs: [observation]})[0]
                action = self.get_action(epoch, q_value)
                new_observation, reward, done, info = self.env.step(action)
                trajectory.append((observation, action, reward, new_observation, done, q_value))
                observation = new_observation
                if len(trajectory) > self.max_trajectory_length:
                    break
            self.add_to_memory(trajectory)

    def create_batch(self):
        batch_q_values = []
        batch_observations = []
        for experience in self.sample_from_memory():
            action = experience['action']
            new_q_value = np.copy(experience['q_value'])
            new_q_value[action] = experience['reward']
            if not experience['done']:
                update_value = np.max(self.sess.run(self.output_layer, {self.inputs: [experience['to']]})[0])
                new_q_value[action] += self.gamma * update_value
            batch_q_values.append(new_q_value)
            batch_observations.append(experience['from'])
        return batch_observations, batch_q_values

    def train(self):
        epoch = 0
        # while loss_value > 0.002:
        while epoch < self.epochs:
            self.generate_new_trajectories(epoch)
            epoch_loss = None
            for sub_epoch_id in range(self.train_per_epoch):
                batch_observations, batch_q_values = self.create_batch()
                _, epoch_loss = self.sess.run((self.train_op, self.loss),
                                              {self.inputs: batch_observations, self.outputs: batch_q_values})
            self.save()
            epoch_total_reward = self.play()
            print(
                "*********** epoch {} ***********\n"
                "memory size: {}, mean state weights: {}\n"
                "total loss: {}\n"
                "total reward gained: {}\n"
                "epsilon: {}".format(epoch, self.experience_replay_memory.shape[0],
                                     np.mean([s['weight'] for s in self.experience_replay_memory]),
                                     epoch_loss, epoch_total_reward, self.get_epsilon(epoch)))
            epoch += 1

    def play(self, render=False, monitor=False, max_timestep=None):
        total_reward = 0
        done = False
        observation = self.env.reset()
        reward = None
        timestep = 0
        if monitor:
            env = wrappers.Monitor(self.env, "./monitors/dqn/", force=True)
        else:
            env = self.env
        while not done:
            if render:
                env.render()
            q_value = self.sess.run(self.test_output_layer, {self.inputs: [observation]})[0]
            # action = env.action_space.sample() # random action
            action = np.argmax(q_value)
            if timestep == self.max_trajectory_length:
                print(total_reward)
                break
            # mod = total_reward % 100
            # if mod in (0, 1, 2, 3):
            # action = env.action_space.sample()
            # action = 1 - action
            observation, reward, done, info = env.step(action)
            if not tr:
                print(f'action selected: {action}, obs: {observation}, reward: {reward}')
            total_reward += reward
            timestep += 1
            if done:
                break
            if max_timestep is not None:
                if timestep > max_timestep:
                    if monitor:
                        env.close()
                        env.reset()
                    break
        return total_reward

    def save(self):
        self.saver.save(self.sess, self.saving_path)

    def load(self):
        import os
        self.sess.run(tf.global_variables_initializer())
        self.sess.run(tf.local_variables_initializer())
        if not os.path.exists(self.saving_path):
            os.makedirs(self.saving_path)
        if not tf.train.checkpoint_exists(self.saving_path + 'checkpoint'):
            print('Saved temp_models not found! Randomly initialized.')
        else:
            self.saver.restore(self.sess, self.saving_path)
            print('Model loaded!')


if __name__ == '__main__':
    tr = False
    model = GymDQNLearner()
    if tr:
        model.train()
    episode_reward = model.play(False, False, 2000)
    print('total reward: %f' % episode_reward)

#
# rewards = []
# total_reward = -1
# while total_reward < 1000:
#     total_reward = model.play(False, False, 2000)
#     rewards.append(total_reward)
#     print('total reward: %f' % total_reward)
#     print('reward mean: %f, std: %f' % (np.mean(rewards), np.std(rewards)))
self.last_obs = next_obs


def update_model(self):
    ### 3. Perform experience replay and train the network.
    # note that this is only done if the replay buffer contains enough samples
    # for us to learn something useful -- until then, the model will not be
    # initialized and random actions should be taken
    if (self.t > self.learning_starts and \
            self.t % self.learning_freq == 0 and \
            self.replay_buffer.can_sample(self.batch_size)):
        # Here, you should perform training. Training consists of four steps:
        # 3.a: use the replay buffer to sample a batch of transitions (see the
        # replay buffer code for function definition, each batch that you sample
        # should consist of current observations, current actions, rewards,
        # next observations, and done indicator).
        # 3.b: initialize the model if it has not been initialized yet; to do
        # that, call
        #    initialize_interdependent_variables(self.session, tf.global_variables(), {
        #        self.obs_t_ph: obs_t_batch,
        #        self.obs_tp1_ph: obs_tp1_batch,
        #    })
        # where obs_t_batch and obs_tp1_batch are the batches of observations at
        # the current and next time step. The boolean variable model_initialized
        # indicates whether or not the model has been initialized.
        # Remember that you have to update the target network too (see 3.d)!
        # 3.c: train the model. To do this, you'll need to use the self.train_fn and
        # self.total_error ops that were created earlier: self.total_error is what you
        # created to compute the total Bellman error in a batch, and self.train_fn
        # will actually perform a gradient step and update the network parameters
        # to reduce total_error. When calling self.session.run on these you'll need to
        # populate the following placeholders:
        # self.obs_t_ph
        # self.act_t_ph
        # self.rew_t_ph
        # self.obs_tp1_ph
        # self.done_mask_ph
        # (this is needed for computing self.total_error)
        # self.learning_rate -- you can get this from self.optimizer_spec.lr_schedule.value(t)
        # (this is needed by the optimizer to choose the learning rate)
        # 3.d: periodically update the target network by calling
        # self.session.run(self.update_target_fn)
        # you should update every target_update_freq steps, and you may find the
        # variable self.num_param_updates useful for this (it was initialized to 0)
        #####

        # YOUR CODE HERE
        # 1. Sample a batch of transitions
        obs_t, act, rew, obs_tp1, done_mask = self.replay_buffer.sample(self.batch_size)

        if not self.model_initialized:
            initialize_interdependent_variables(self.session, tf.global_variables(),
                                                {self.obs_t_ph: obs_t, self.obs_tp1_ph: obs_tp1, })
        self.model_initialized = True

        # training model
        feed_dict = {self.obs_t_ph: obs_t,
                     self.act_t_ph: act,
                     self.rew_t_ph: rew,
                     self.obs_tp1_ph: obs_tp1,
                     self.done_mask_ph: done_mask,
                     self.learning_rate: self.optimizer_spec.lr_schedule.value(self.t)
                     }
        self.session.run(self.train_fn, feed_dict=feed_dict)

        if self.num_param_updates % self.target_update_freq == 0:
            self.session.run(self.update_target_fn)

        self.num_param_updates += 1

    self.t += 1


def log_progress(self):
    episode_rewards = self.env.episode_rewards

    if len(episode_rewards) > 0:
        self.mean_episode_reward = np.mean(episode_rewards[-100:])

    if len(episode_rewards) > 100:
        self.best_mean_episode_reward = max(self.best_mean_episode_reward, self.mean_episode_reward)

    if self.t % self.log_every_n_steps == 0 and self.model_initialized:
        print("Timestep %d" % (self.t,))
        print("mean reward (100 episodes) %f" % self.mean_episode_reward)
        print("best mean reward %f" % self.best_mean_episode_reward)
        print("episodes %d" % len(episode_rewards))
        print("exploration %f" % self.exploration.value(self.t))
        print("learning_rate %f" % self.optimizer_spec.lr_schedule.value(self.t))
        if self.start_time is not None:
            print("running time %f" % ((time.time() - self.start_time) / 60.))

        self.start_time = time.time()

        sys.stdout.flush()

        # Record accurate time stamp and reward
        self.timestep_log.append(self.t)
        self.mean_reward_log.append(self.mean_episode_reward)
        self.best_reward_log.append(self.best_mean_episode_reward)

        log = {'Timestep': np.array(self.timestep_log),
               'mean': np.array(self.mean_reward_log),
               'best': np.array(self.best_reward_log)}

        with open(self.rew_file, 'wb') as f:
            pickle.dump(log, f, pickle.HIGHEST_PROTOCOL)


def learn(*args, **kwargs):
    alg = QLearner(*args, **kwargs)
    while not alg.stopping_criterion_met():
        alg.step_env()
        # at this point, the environment should have been advanced one step (and
        # reset if done was true), and self.last_obs should point to the new latest
        # observation
        alg.update_model()
        alg.log_progress()
