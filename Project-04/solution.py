import numpy as np
import matplotlib.pyplot as plt

import time

import scipy.signal
from gym.spaces import Box, Discrete

import torch
from torch.optim import Adam
import torch.nn as nn
from torch.distributions.categorical import Categorical

def discount_cumsum(x, discount):
    """
    Compute  cumulative sums of vectors.

    Input: [x0, x1, ..., xn]
    Output: [x0 + discount * x1 + discount^2 * x2 + ... , x1 + discount * x2 + ... , ... , xn]
    """
    return scipy.signal.lfilter([1], [1, float(-discount)], x[::-1], axis=0)[::-1]

def combined_shape(length, shape=None):
    """Helper function that combines two array shapes."""
    if shape is None:
        return (length,)
    return (length, shape) if np.isscalar(shape) else (length, *shape)

def mlp(sizes, activation, output_activation=nn.Identity):
    """The basic multilayer perceptron architecture used."""
    layers = []
    for j in range(len(sizes)-1):
        act = activation if j < len(sizes)-2 else output_activation
        layers += [nn.Linear(sizes[j], sizes[j+1]), act()]
    return nn.Sequential(*layers)


class MLPCategoricalActor(nn.Module):
    """A class for the policy network."""

    def __init__(self, obs_dim, act_dim, hidden_sizes, activation):
        super().__init__()
        self.logits_net = mlp([obs_dim] + list(hidden_sizes) + [act_dim], activation)

    def _distribution(self, obs):
        """Takes the observation and outputs a distribution over actions."""
        logits = self.logits_net(obs)
        return Categorical(logits=logits)

    def _log_prob_from_distribution(self, pi, act):
        """
        Takes a distribution and action, then gives the log-probability of the action
        under that distribution.
        """
        return pi.log_prob(act)

    def forward(self, obs, act=None):
        """
        Produce action distributions for given observations, and then compute the
        log-likelihood of given actions under those distributions.
        """
        pi = self._distribution(obs)
        logp_a = None
        if act is not None:
            logp_a = self._log_prob_from_distribution(pi, act)
        return pi, logp_a


class MLPCritic(nn.Module):
    """The network used by the value function."""
    def __init__(self, obs_dim, hidden_sizes, activation):
        super().__init__()
        self.v_net = mlp([obs_dim] + list(hidden_sizes) + [1], activation)

    def forward(self, obs):
        # Critical to ensure v has right shape
        return torch.squeeze(self.v_net(obs), -1)



class MLPActorCritic(nn.Module):
    """Class to combine policy (actor) and value (critic) function neural networks."""

    def __init__(self,
                 hidden_sizes=(64,64), activation=nn.Tanh):
        super().__init__()

        obs_dim = 8

        # Build policy for 4-dimensional action space
        self.pi = MLPCategoricalActor(obs_dim, 4, hidden_sizes, activation)

        # Build value function
        self.v  = MLPCritic(obs_dim, hidden_sizes, activation)

    def step(self, state):
        """
        Take a state and return an action, value function, and log-likelihood
        of chosen action.
        """
        # TODO1: Implement this function.
        # It is supposed to return three numbers:
        #    1. An action sampled from the policy given a state (0, 1, 2 or 3)
        #    2. The value function at the given state
        #    3. The log-probability of the action under the policy output distribution
        # Hint: This function is only called when interacting with the environment. You should use
        # `torch.no_grad` to ensure that it does not interfere with the gradient computation.

        with torch.no_grad():
            # compute the distribution & the action
            distr = self.pi._distribution(state)
            act   = distr.sample()
            # compute the value function
            val_func = self.v.forward(state)
            # compute the log probability
            log_prob = self.pi._log_prob_from_distribution(distr, act)

            return act.item(), val_func.item(), log_prob.item()


class VPGBuffer:
    """
    Buffer to store trajectories.
    """
    def __init__(self, obs_dim, act_dim, size, gamma, lam):
        self.obs_buf = np.zeros(combined_shape(size, obs_dim), dtype=np.float32)
        self.act_buf = np.zeros(combined_shape(size, act_dim), dtype=np.float32)
        # advantage estimates
        self.phi_buf = np.zeros(size, dtype=np.float32)
        # rewards
        self.rew_buf = np.zeros(size, dtype=np.float32)
        # trajectory's remaining return
        self.ret_buf = np.zeros(size, dtype=np.float32)
        # values predicted
        self.val_buf = np.zeros(size, dtype=np.float32)
        # log probabilities of chosen actions under behavior policy
        self.logp_buf = np.zeros(size, dtype=np.float32)
        self.gamma = gamma
        self.lam = lam
        self.ptr, self.path_start_idx, self.max_size = 0, 0, size

    def store(self, obs, act, rew, val, logp):
        """
        Append a single timestep to the buffer. This is called at each environment
        update to store the outcome observed.
        """
        # buffer has to have room so you can store
        assert self.ptr < self.max_size
        self.obs_buf[self.ptr] = obs
        self.act_buf[self.ptr] = act
        self.rew_buf[self.ptr] = rew
        self.val_buf[self.ptr] = val
        self.logp_buf[self.ptr] = logp
        self.ptr += 1

    def end_traj(self, last_val=0):
        """
        Call after a trajectory ends. Last value is value(state) if cut-off at a
        certain state, or 0 if trajectory ended uninterrupted
        """
        path_slice = slice(self.path_start_idx, self.ptr)
        rews = np.append(self.rew_buf[path_slice], last_val)
        vals = np.append(self.val_buf[path_slice], last_val)

        # TODO6: Implement computation of phi.
        
        # Hint: For estimating the advantage function to use as phi, equation 
        # 16 in the GAE paper (see task description) will be helpful, and so will
        # the discout_cumsum function at the top of this file. 
        value_current = vals[:-1]
        value_next = vals[1:]
        
        advantage = rews[:-1] + value_next - value_current
        self.phi_buf[path_slice] = discount_cumsum(advantage, self.gamma*self.lam)

        #TODO4: currently the return is the total discounted reward for the whole episode. 
        # Replace this by computing the reward-to-go for each timepoint.
        # Hint: use the discount_cumsum function.

        # Previous version implementing the discount sum without reward to go
        #self.ret_buf[path_slice] = discount_cumsum(rews, self.gamma)[0] * np.ones(self.ptr-self.path_start_idx) 
        # New version implementinf reward to go
        self.ret_buf[path_slice] = discount_cumsum(rews, self.gamma)[0:self.ptr-self.path_start_idx]


        self.path_start_idx = self.ptr


    def get(self):
        """
        Call after an epoch ends. Resets pointers and returns the buffer contents.
        """
        # Buffer has to be full before you can get something from it.
        assert self.ptr == self.max_size
        self.ptr, self.path_start_idx = 0, 0

        # TODO7: Here it may help to normalize the values in self.phi_buf
        self.phi_buf /= np.linalg.norm(self.phi_buf)

        data = dict(obs=self.obs_buf, act=self.act_buf, ret=self.ret_buf,
                    phi=self.phi_buf, logp=self.logp_buf)
        return {k: torch.as_tensor(v, dtype=torch.float32) for k,v in data.items()}


class Agent:
    def __init__(self, env):
        self.env = env
        self.hid = 64  # layer width of networks
        self.l = 2  # layer number of networks
        # initialises an actor critic
        self.ac = MLPActorCritic(hidden_sizes=[self.hid]*self.l)

        # Learning rates for policy and value function
        pi_lr = 3e-3
        vf_lr = 1e-3

        # we will use the Adam optimizer to update value function and policy
        self.pi_optimizer = Adam(self.ac.pi.parameters(), lr=pi_lr)
        self.v_optimizer = Adam(self.ac.v.parameters(), lr=vf_lr)

    def pi_update(self, data):
        """
        Use the data from the buffer to update the policy. Returns nothing.
        """
        #TODO2: Implement this function. 
        #TODO8: Change the update rule to make use of the baseline instead of rewards-to-go.

        obs = data['obs']
        act = data['act']
        phi = data['phi']
        ret = data['ret']

        # Before doing any computation, always call.zero_grad on the relevant optimizer

        #Hint: you need to compute a 'loss' such that its derivative with respect to the policy
        #parameters is the policy gradient. Then call loss.backwards() and pi_optimizer.step()

        # reset the gradients
        self.pi_optimizer.zero_grad()
        # forward pass
        _, logp = self.ac.pi.forward(obs, act)
        # compute the loss
        policy_loss = -(logp * phi).mean()
        # backpropagate the gradients
        policy_loss.backward()
        # optimize the parameters 
        self.pi_optimizer.step()

        return

    def v_update(self, data):
        """
        Use the data from the buffer to update the value function. Returns nothing.
        """
        #TODO5: Implement this function

        obs = data['obs']
        act = data['act']
        phi = data['phi']
        ret = data['ret']

        # Hint: it often works well to do multiple rounds of value function updates per epoch.
        # With the learning rate given, we'd recommend 100. 
        # In each update, compute a loss for the value function, call loss.backwards() and 
        # then v_optimizer.step()
        # Before doing any computation, always call.zero_grad on the relevant optimizer
        self.v_optimizer.zero_grad()

        for _ in range(100):
            # clean gradients
            self.v_optimizer.zero_grad()
            # get rewards 
            rewards = ret
            # get values
            values = self.ac.v(obs)
            # add a loss function & compute loss
            loss = nn.MSELoss()
            value_loss = loss(rewards,values)
            # backpropagate loss
            value_loss.backward()
            # optimize parameters 
            self.v_optimizer.step()

        return

    def train(self):
        """
        Main training loop.

        IMPORTANT: This function is called by the checker to train your agent.
        You SHOULD NOT change the arguments this function takes and what it outputs!
        """

        # The observations are 8 dimensional vectors, and the actions are numbers,
        # i.e. 0-dimensional vectors (hence act_dim is an empty list).
        obs_dim = [8]
        act_dim = []

        # Training parameters
        # You may wish to change the following settings for the buffer and training
        # Number of training steps per epoch
        steps_per_epoch = 3000
        # Number of epochs to train for
        epochs = 50
        # The longest an episode can go on before cutting it off
        max_ep_len = 300
        # Discount factor for weighting future rewards
        gamma = 0.99
        lam = 0.97

        # Set up buffer
        buf = VPGBuffer(obs_dim, act_dim, steps_per_epoch, gamma, lam)

        # Initialize the ADAM optimizer using the parameters
        # of the policy and then value networks

        # Initialize the environment
        state, ep_ret, ep_len = self.env.reset(), 0, 0

        # Main training loop: collect experience in env and update / log each epoch
        for epoch in range(epochs):
            ep_returns = []
            for t in range(steps_per_epoch):
                a, v, logp = self.ac.step(torch.as_tensor(state, dtype=torch.float32))

                next_state, r, terminal = self.env.transition(a)
                ep_ret += r
                ep_len += 1

                # Log transition
                buf.store(state, a, r, v, logp)

                # Update state (critical!)
                state = next_state

                timeout = ep_len == max_ep_len
                epoch_ended = (t == steps_per_epoch - 1)

                if terminal or timeout or epoch_ended:
                    # if trajectory didn't reach terminal state, bootstrap value target
                    if epoch_ended:
                        _, v, _ = self.ac.step(torch.as_tensor(state, dtype=torch.float32))
                    else:
                        v = 0
                    if timeout or terminal:
                        ep_returns.append(ep_ret)  # only store return when episode ended
                    buf.end_traj(v)
                    state, ep_ret, ep_len = self.env.reset(), 0, 0

            mean_return = np.mean(ep_returns) if len(ep_returns) > 0 else np.nan
            if len(ep_returns) == 0:
                print(f"Epoch: {epoch+1}/{epochs}, all episodes exceeded max_ep_len")
            print(f"Epoch: {epoch+1}/{epochs}, mean return {round(mean_return, 2)}")

            # This is the end of an epoch, so here is where we update the policy and value function

            data = buf.get()

            self.pi_update(data)
            self.v_update(data)


        return True


    def get_action(self, obs):
        """
        Sample an action from your policy.

        IMPORTANT: This function is called by the checker to evaluate your agent.
        You SHOULD NOT change the arguments this function takes and what it outputs!
        It is not used in your own training code. Instead the .step function in 
        MLPActorCritic is used since it also outputs relevant side-information. 
        """
        # TODO3: Implement this function.
        # Currently, this just returns a random action.
        # convert the obervation into a tensor 

        action = self.ac.pi(torch.tensor(obs).float())

        return action[0].sample().item()


def main():
    """
    Train and evaluate agent.

    This function basically does the same as the checker that evaluates your agent.
    You can use it for debugging your agent and visualizing what it does.
    """
    from lunar_lander import LunarLander
    from gym.wrappers.monitoring.video_recorder import VideoRecorder

    env = LunarLander()

    agent = Agent(env)
    agent.train()

    rec = VideoRecorder(env, "policy.mp4")
    episode_length = 300
    n_eval = 100
    returns = []
    print("Evaluating agent...")

    for i in range(n_eval):
        print(f"Testing policy: episode {i+1}/{n_eval}")
        state = env.reset()
        cumulative_return = 0
        # The environment will set terminal to True if an episode is done.
        terminal = False
        env.reset()
        for t in range(episode_length):
            if i <= 10:
                rec.capture_frame()
            # Taking an action in the environment
            action = agent.get_action(state)
            state, reward, terminal = env.transition(action)
            cumulative_return += reward
            if terminal:
                break
        returns.append(cumulative_return)
        print(f"Achieved {cumulative_return:.2f} return.")
        if i == 10:
            rec.close()
            print("Saved video of 10 episodes to 'policy.mp4'.")
    env.close()
    print(f"Average return: {np.mean(returns):.2f}")

if __name__ == "__main__":
    main()
