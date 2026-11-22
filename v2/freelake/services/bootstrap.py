"""
启动引导（freelake/services/bootstrap.py）
=========================================

首次启动按 `const.announcement_tags` 发布公告帖；靠标题唯一性保证幂等，
已存在就跳过。
"""

from const import announcement_tags
from freelake.config import ANNOUNCEMENT_POST_TITLE
from freelake.db.models import PostModel as _Post
from freelake.db.models import UserModel as _User
from freelake.services.posts import Post
from freelake.settings import admin_credentials


def bootstrap_announcements() -> None:
    """自动发布「迎新 + 社区规范」合订公告（幂等）。

    逻辑与「启动引导：创建管理员账号」一致：在模块导入（站点启动）时执行，
    用「管理员 + 定稿标题」判断是否已存在，避免每次启动重复发布。
    """
    admin_id = admin_credentials().get("userid")
    # 仅当管理员账号确实已存在时才以此身份发布公告（作为作者）。
    # 这样既符合语义，也兼容测试环境（conftest 提供的 admin 无密码、
    # 不会真正创建账号，避免导入测试库时误写入公告文章）。
    if not admin_id or _User.get_or_none(_User.userid == admin_id) is None:
        return
    if _Post.get_or_none((_Post.author_id == admin_id) & (_Post.title == ANNOUNCEMENT_POST_TITLE)):
        return

    Post.publish(
        authorid=admin_id,
        title=ANNOUNCEMENT_POST_TITLE,
        content="\n".join(
            [
                "# 🎉 欢迎加入 FreeLake",
                "",
                "FreeLake 是一个属于大家的校园交流社区。在这里，你可以分享校园生活的点滴、",
                "交流学习经验、发布二手与寻物信息，也可以找到志同道合的伙伴。",
                "",
                "## 你可以做什么",
                "- **发布文章**：分享你的见闻、经验与想法",
                "- **点赞 & 收藏**：支持你喜欢的帖子，方便日后回看",
                "- **评论互动**：与同学们一起讨论交流",
                "- **附带上传**：图文并茂，让内容更丰富",
                "",
                "## 开始使用",
                "1. 注册账号并登录",
                "2. 在「发布文章」页面写点东西分享给大家",
                "3. 到首页逛逛，看看大家都在聊什么",
                "",
                "---",
                "",
                "# 📜 社区规范",
                "",
                "为了维护友善、有序的交流环境，请每位成员遵守以下规范。",
                "",
                "## 一、基本守则",
                "1. 尊重他人，友善交流，禁止辱骂、人身攻击与歧视性言论。",
                "2. 遵守法律法规与校规校纪，不发布违法、违规内容。",
                "3. 保护个人隐私，未经允许不得公开他人隐私信息。",
                "",
                "## 二、内容规范",
                "1. 发布内容须与主题相关，请勿刷屏、灌水。",
                "2. 广告、外链推广等商业信息请提前联系管理员。",
                "3. 转载内容请注明出处，尊重原创。",
                "",
                "## 三、违规处理",
                "- 违反规范的内容将被删除；多次违规者将被限制或封禁账号。",
                "- 如发现违规内容，欢迎向管理员举报。",
                "",
                "感谢每一位成员的理解与配合，让我们一起把 FreeLake 建设得更好！💙",
            ]
        ),
        attachments=[],
        tags=announcement_tags,
    )


# 注意：不要在这里调用 bootstrap_announcements()——本模块被 api.py 导入时
# 数据库可能还没建表；调用点固定在 api.py 启动装配的最后一行。
