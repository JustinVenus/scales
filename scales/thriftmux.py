import functools
import inspect
import types

from thrift.transport import TTransport
from thrift.transport import TSocket

from scales import thriftmuxsink
from scales.core import Scales
from scales.pool import RoundRobinPoolMemberSelector
from scales.varzsocketwrapper import VarzSocketWrapper

_MUXERS = {}
class ThriftMuxSocketTransportSinkProvider(object):
  @staticmethod
  def AreTransportsSharable():
    return True

  def _CreateSocket(self, host, port):
    return TSocket.TSocket(host, port)

  def GetConnection(self, server, pool_name, health_cb):
    key = (server, pool_name)
    if key in _MUXERS:
      sink, cbs = _MUXERS[key]
    else:
      sock = self._CreateSocket(server.host, server.port)
      healthy_sock = VarzSocketWrapper(sock, pool_name)
      sink = thriftmuxsink.ThriftMuxSocketTransportSink(healthy_sock)
      cbs = set()
      _MUXERS[key] = (sink, cbs)

    if health_cb not in cbs:
      cbs.add(health_cb)
      sink.shutdown_result.rawlink(lambda ar: health_cb(server))
    return sink

  @staticmethod
  def IsConnectionFault(e):
    return isinstance(e,  TTransport.TTransportException)


class ThriftMuxMessageSinkProvider(object):
  @staticmethod
  def CreateMessageSinks():
    return [
      thriftmuxsink.TimeoutSink(),
      thriftmuxsink.ThrfitMuxMessageSerializerSink()
    ]


class ThriftServiceProvider(object):
  @staticmethod
  def CreateServiceClient(Client, dispatcher):
    """Creates a proxy class that takes all method on Client
    and sends them to a dispatcher.

    Args:
      Client - A class object implementing one or more thrift interfaces.
      dispatcher - An instance of a MessageDispatcher.
    """

    def ProxyMethod(method_name, orig_method):
      @functools.wraps(orig_method)
      def _ProxyMethod(self, *args, **kwargs):
        ar = dispatcher.DispatchMethodCall(Client, method_name, args)
        return ar.get()
      return types.MethodType(_ProxyMethod, Client)

    # Find the thrift interface on the client
    iface = next(b for b in Client.__bases__ if b.__name__ == 'Iface')
    is_thrift_method = lambda m: inspect.ismethod(m) and not inspect.isbuiltin(m)

    # Find all methods on the thrift interface
    iface_methods = dir(iface)
    is_iface_method = lambda m: m and is_thrift_method(m) and m.__name__ in iface_methods

    # Then get the methods on the client that it implemented from the interface
    client_methods = { m[0]: ProxyMethod(*m)
                       for m in inspect.getmembers(Client, is_iface_method) }

    # Create a proxy class to intercept the thrift methods.
    proxy = type(
      '_ScalesTransparentProxy<%s>' % Client.__module__,
      (iface, object),
      client_methods)
    return proxy


class ThriftMux(object):
  @staticmethod
  def newClient(Client, uri):
    """Create a new client for a ThriftMux service.

    Args:
      Client - The plain Thrift client (generated by the thrift compiler.)
      uri - The URI of the service.  Uri may be in the form of
            "tcp://host:port,host:port,...", or "zk://host:port/server/set/path".

    Returns:
      A proxy implementing all thrift methods of Client.
    """
    return Scales \
      .newBuilder(Client) \
      .setPoolMemberSelector(RoundRobinPoolMemberSelector()) \
      .setMessageSinkProvider(ThriftMuxMessageSinkProvider()) \
      .setTransportSinkProvider(ThriftMuxSocketTransportSinkProvider()) \
      .setUri(uri) \
      .setTimeout(5) \
      .setServiceProvider(ThriftServiceProvider()) \
      .build()
