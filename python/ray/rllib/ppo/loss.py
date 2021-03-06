from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import gym.spaces
import tensorflow as tf

from ray.rllib.models import ModelCatalog


class ProximalPolicyLoss(object):

    def __init__(
            self, observation_space, action_space,
            observations, returns, advantages, actions,
            prev_logits, prev_vf_preds, logit_dim,
            kl_coeff, distribution_class, config, sess):
        assert (isinstance(action_space, gym.spaces.Discrete) or
                isinstance(action_space, gym.spaces.Box))
        self.prev_dist = distribution_class(prev_logits)

        # Saved so that we can compute actions given different observations
        self.observations = observations

        self.curr_logits = ModelCatalog.get_model(
            observations, logit_dim, config["model"]).outputs
        self.curr_dist = distribution_class(self.curr_logits)
        self.sampler = self.curr_dist.sample()

        if config["use_gae"]:
            vf_config = config["model"].copy()
            # Do not split the last layer of the value function into
            # mean parameters and standard deviation parameters and
            # do not make the standard deviations free variables.
            vf_config["free_logstd"] = False
            with tf.variable_scope("value_function"):
                self.value_function = ModelCatalog.get_model(
                    observations, 1, vf_config).outputs
            self.value_function = tf.reshape(self.value_function, [-1])

        # Make loss functions.
        self.ratio = tf.exp(self.curr_dist.logp(actions) -
                            self.prev_dist.logp(actions))
        self.kl = self.prev_dist.kl(self.curr_dist)
        self.mean_kl = tf.reduce_mean(self.kl)
        self.entropy = self.curr_dist.entropy()
        self.mean_entropy = tf.reduce_mean(self.entropy)
        self.surr1 = self.ratio * advantages
        self.surr2 = tf.clip_by_value(self.ratio, 1 - config["clip_param"],
                                      1 + config["clip_param"]) * advantages
        self.surr = tf.minimum(self.surr1, self.surr2)
        self.mean_policy_loss = tf.reduce_mean(-self.surr)

        if config["use_gae"]:
            # We use a huber loss here to be more robust against outliers,
            # which seem to occur when the rollouts get longer (the variance
            # scales superlinearly with the length of the rollout)
            self.vf_loss1 = tf.square(self.value_function - returns)
            vf_clipped = prev_vf_preds + tf.clip_by_value(
                self.value_function - prev_vf_preds,
                -config["clip_param"], config["clip_param"])
            self.vf_loss2 = tf.square(vf_clipped - returns)
            self.vf_loss = tf.minimum(self.vf_loss1, self.vf_loss2)
            self.mean_vf_loss = tf.reduce_mean(self.vf_loss)
            self.loss = tf.reduce_mean(
                -self.surr + kl_coeff * self.kl +
                config["vf_loss_coeff"] * self.vf_loss -
                config["entropy_coeff"] * self.entropy)
        else:
            self.mean_vf_loss = tf.constant(0.0)
            self.loss = tf.reduce_mean(
                -self.surr +
                kl_coeff * self.kl -
                config["entropy_coeff"] * self.entropy)

        self.sess = sess

        if config["use_gae"]:
            self.policy_results = [
                self.sampler, self.curr_logits, self.value_function]
        else:
            self.policy_results = [
                self.sampler, self.curr_logits, tf.constant("NA")]

    def compute(self, observations):
        return self.sess.run(self.policy_results,
                             feed_dict={self.observations: observations})

    def loss(self):
        return self.loss
