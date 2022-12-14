from __future__ import annotations
import tensorflow as tf
from collections import UserDict
import typing
from typing import Callable, TypeVar, Optional, Tuple


T = TypeVar('T')
U = TypeVar('U')
V = TypeVar('V')
KT = TypeVar('KT')
VT = TypeVar('VT')


# Custom dictionary class
class FunctionDict(UserDict, typing.Mapping[KT, VT]):
    """
    This custom dictionary class has a few differences compared to a normal dict. It only accepts ``Callable`` items.
    In particular, a ``tf.keras.layers.Layer`` object is a ``Callable`` that is  processed differently than any other
    ``Callable`` object.

    ``__setitem__`` interface
    --------------------------

    - A ``tf.keras.layers.Layer`` is stored in the dictionary wrapped in a one-argument lambda that discards its
      argument and simply returns it.
    - Any other ``Callable`` object is stored as is.
    - An attempt to save a non-``Callable`` object results in a ``ValueError`` exception.

    ``__getitem__`` interface
    --------------------------
    The given key is first parsed using the ``parse_key`` method. The key can either be of the form 'xyz[a]' or 'xyz':

    - A key of the form 'xyz' is parsed as is
    - A key of the form 'xyz[a]' is split into the substring before the square brackets 'xyz' and the substring in the
      square brackets 'a'.
    We call the first substring ``true_key`` and the second substring ``arg``.

    Once the key is parsed, we obtain the item in the dictionary using ``true_key``. The dictionary does not return the
    ``Callable`` object directly, but instead returns the output of the application of ``arg`` to the ``Callable``.

    """
    @staticmethod
    def parse_key(key: str) -> Tuple[str, Optional[str]]:
        """
        Parses a string of the form 'xyz[a]' or 'xyz'.

        - 'xyz[a]' is parsed to a tuple (xyz, a)
        - 'xyz' is parsed to a tuple (xyz, None)

        :param key: String to parse.
        :return: Tuple that contains the substring before the square brackets and, if present, the
                 substring inside the square brackets or ``None``.
        """
        tokens = key.split('[')
        true_key = tokens[0]
        arg = None if len(tokens) == 1 else tokens[1][: tokens[1].find(']')]
        return true_key, arg

    def __getitem__(self, key):
        true_key, arg = self.parse_key(key)
        return self.data[true_key](arg)

    def __setitem__(self, key, value):
        if isinstance(value, tf.keras.layers.Layer):
            self.data[key] = lambda _: value
        elif callable(value):
            self.data[key] = value
        else:
            raise ValueError("Invalid item:", str(value))


class Psi(tf.keras.layers.Layer):
    def __init__(self, single_op: Optional[Callable[[tf.Tensor[T]], tf.Tensor[U]]] = None,
                 multiple_op: Optional[Callable[[tf.Tensor[T], tf.Tensor[int]], tf.Tensor[U]]] = None, **kwargs):
        """
        A general function applied on node labels f: (T*, T) -> U. For single graph datasets, which use the
        SingleGraphLoader, only the single_op parameter is necessary. For multiple graph datasets, using the
        MultipleGraphLoader, only the multiple_op parameter is necessary. The multiple_op argument is a function which
        takes an additional parameter to distinguish which values in the first argument refer to which graph. For
        more information, refer to the disjoint data mode in the Spektral library documentation.

        :param single_op: A function that transforms a Tensor of node labels of type T into a node label of type U.
         The function must be compatible with Tensorflow's broadcasting rules. The function takes only one argument of
         type Tensor[T] and uses broadcasting to emulate the tuple (T*, T) in the definition of f.
        :param multiple_op: A function that transforms a Tensor of node labels of type T and a Tensor of their
         respective graph indices of type int64 to a node label of type U. The function must be compatible with
         Tensorflow's broadcasting rules. The function must use broadcasting to emulate the tuple (T*, T) in the
         definition of f.
        """
        super().__init__(**kwargs)
        if single_op is not None:
            setattr(self, 'single_op', single_op)
        if multiple_op is not None:
            setattr(self, 'multiple_op', multiple_op)

    def single_op(self, x):
        raise NotImplementedError

    def multiple_op(self, x, i):
        raise NotImplementedError

    def __call__(self, x, i=None):
        if i is not None:
            return self.multiple_op(x, i)
        else:
            return self.single_op(x)


class PsiLocal(Psi):
    def __init__(self, f: Optional[Callable[[tf.Tensor[T]], tf.Tensor[U]]] = None, **kwargs):
        """
        A local transformation of node labels f: T -> U

        :param f: A function that transforms a Tensor of node labels of type T to a Tensor of node labels of type U.
         The function must be compatible with Tensorflow's broadcasting rules.
        """
        if f is not None:
            setattr(self, 'f', f)
        super().__init__(single_op=self.f, **kwargs)

    def f(self, x):
        raise NotImplementedError

    single_op = f
    multiple_op = None

    def __call__(self, x, i=None):
        return self.single_op(x)


class PsiGlobal(Psi):
    def __init__(self, single_op: Optional[Callable[[tf.Tensor[T]], tf.Tensor[U]]] = None,
                 multiple_op: Optional[Callable[[tf.Tensor[T], tf.Tensor[int]], tf.Tensor[U]]] = None, **kwargs):
        """
        A global pooling operation on node labels f: T* -> U. For single graph datasets, which use the
        SingleGraphLoader, only the single_op parameter is necessary. For multiple graph datasets, using the
        MultipleGraphLoader, only the multiple_op parameter is necessary. The multiple_op argument is a function which
        takes an additional parameter to distinguish which values in the first argument refer to which graph. For
        more information, refer to the disjoint data mode in the Spektral library documentation. The Tensor output of
        both ``single_op`` and ``multiple_op`` is broadcast automatically to label all nodes in the graph with the
        pooled Tensor value for that graph

        :param single_op: A function that transforms a Tensor of node labels of type T to a Tensor of node labels of
         type U.
        :param multiple_op: A function that transforms a Tensor of node labels of type T and a Tensor of their
         respective graph indices of type int64 to Tensor of node labels of type U.
        """
        if single_op is not None:
            setattr(self, 'single_op', single_op)
        if multiple_op is not None:
            setattr(self, 'multiple_op', multiple_op)
        super().__init__(single_op=self.single_op, multiple_op=self.multiple_op, **kwargs)

    def single_op(self, x):
        raise NotImplementedError

    def multiple_op(self, x, i):
        raise NotImplementedError

    def __call__(self, x, i=None):
        if i is not None:
            output = self.multiple_op(x, i)
            _, _, count = tf.unique_with_counts(i)
            return tf.repeat(output, count, axis=0)
        else:
            output = self.single_op(x)
            return tf.repeat(output, tf.shape(x)[0], axis=0)


class Phi(tf.keras.layers.Layer):
    def __init__(self, f: Optional[Callable[[tf.Tensor[T], tf.Tensor[U], tf.Tensor[T]], tf.Tensor[V]]] = None,
                 **kwargs):
        """
         A function  f: (T, U, T) -> V to compute the message sent by a node i to a node j through edge e.

        :param f: A function applied on a triple composed of a Tensor of source node labels of type T, a Tensor of edge
         labels of type U, and a Tensor of target node labels of type T that returns a Tensor of node labels of type V.
        """
        super().__init__(**kwargs)
        if f is not None:
            setattr(self, 'f', f)

    def f(self, src, e, tgt):
        raise NotImplementedError

    def __call__(self, src, e, tgt):
        return self.f(src, e, tgt)


class Sigma(tf.keras.layers.Layer):
    def __init__(self, f: Optional[Callable[[tf.Tensor[T], tf.Tensor[int], int, tf.Tensor[U]], tf.Tensor[V]]] = None,
                 **kwargs):
        """
        A function f: (T*, U) -> V to aggregate the messages sent to a node, including the current label of the node.

        :param f: A function of four arguments: a Tensor of messages of type T, a Tensor of integer indices that specify
        the id of the node each message is being sent to, an integer that specify the total number of nodes in the
        graph and finally a Tensor of node labels of type U. The function must return a Tensor of node labels of type V.
        """
        super().__init__(**kwargs)
        if f is not None:
            setattr(self, 'f', f)

    def f(self, m, i, n, x):
        raise NotImplementedError

    def __call__(self, m, i, n, x):
        return self.f(m, i, n, x)
