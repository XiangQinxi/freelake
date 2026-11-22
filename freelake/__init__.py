"""FreeLake v2 应用包。

分层约定（单向依赖）::

    web → services → repos → db

``db`` 子包不依赖 streamlit；``api.py`` 暂时作为兼容门面，页面可逐页迁移。
"""

__version__ = "4.0.0.dev0"
