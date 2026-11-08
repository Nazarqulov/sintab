from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.menu import home_button
from app.core.config import settings
from app.core.i18n import LANGUAGE_NAMES, LANGUAGES, t
from app.core.permissions import Permission
from app.db.models import Depot, Group, RoutingRule, User
from app.services import groups as svc
from app.services.alerts.formatter import type_label
from app.services.alerts.types import AlertType, Severity
from app.services.depots import distinct_tags, list_depots
from app.services.groups import GroupError
from app.services.users import has_permission

router = Router(name="groups")

MANAGE = {"permission": Permission.MANAGE_GROUPS}
GROUP_CHATS = {ChatType.GROUP, ChatType.SUPERGROUP}
ANY = "*"
MAX_TAGS = 30


class GroupAdmin(CallbackData, prefix="gr"):
    action: str
    group_id: int = 0
    arg: str | None = None


def _btn(text: str, action: str, group_id: int = 0, arg: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=GroupAdmin(action=action, group_id=group_id, arg=arg).pack())


def _rows(buttons: list[InlineKeyboardButton], width: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + width] for i in range(0, len(buttons), width)]


# --- Guruh ichida: /bind, /unbind ---

def _topic_id(message: Message) -> int | None:
    return message.message_thread_id if message.is_topic_message else None


@router.message(Command("bind"), F.chat.type.in_(GROUP_CHATS), flags=MANAGE)
async def cmd_bind(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    try:
        group, created = await svc.bind_group(
            session, user, message.chat.id, _topic_id(message), message.chat.title, settings.default_language
        )
    except GroupError as error:
        await message.reply(error.render(lang))
        return
    key = "groups.bound" if created else "groups.already_bound"
    await message.reply(t(key, lang, title=escape(group.title or str(group.chat_id))))


@router.message(Command("unbind"), F.chat.type.in_(GROUP_CHATS), flags=MANAGE)
async def cmd_unbind(message: Message, session: AsyncSession, user: User, lang: str) -> None:
    group = await svc.find_bound_group(session, message.chat.id, _topic_id(message))
    if group is None:
        await message.reply(t("groups.not_bound", lang))
        return
    title = await svc.unbind_group(session, user, group.id)
    await message.reply(t("groups.unbound", lang, title=escape(title)))


@router.message(Command("bind", "unbind"), F.chat.type == ChatType.PRIVATE, flags=MANAGE)
async def cmd_bind_private(message: Message, lang: str) -> None:
    await message.answer(t("groups.bind_in_group", lang))


# --- Shaxsiy chatda: /groups ---

async def list_view(session: AsyncSession, page: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    groups, pages = await svc.list_groups(session, page)
    lines = [t("groups.title", lang), "", t("groups.how_to_bind", lang)]
    buttons = []
    for group in groups:
        rules = len(await svc.group_rules(session, group.id))
        icon = "⏰" if group.is_escalation else "💬"
        buttons.append([_btn(f"{icon} {group.title or group.chat_id} · {rules}", "view", group.id)])
    if not groups:
        lines += ["", t("groups.empty", lang)]
    nav = []
    if page > 0:
        nav.append(_btn("◀️", "list", arg=str(page - 1)))
    if page < pages - 1:
        nav.append(_btn("▶️", "list", arg=str(page + 1)))
    buttons += [nav, [home_button(lang)]]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[row for row in buttons if row])


async def show_groups(target: Message, session: AsyncSession, lang: str) -> None:
    text, markup = await list_view(session, 0, lang)
    await target.answer(text, reply_markup=markup)


@router.message(Command("groups"), F.chat.type == ChatType.PRIVATE, flags=MANAGE)
async def cmd_groups(message: Message, session: AsyncSession, lang: str) -> None:
    await show_groups(message, session, lang)


def rule_line(rule: RoutingRule, depots: dict[int, str], lang: str) -> str:
    any_text = t("common.any", lang)
    return " · ".join(
        [
            f"#{rule.id}",
            type_label(rule.alert_type, lang) if rule.alert_type else f"{t('groups.rule_type', lang)}: {any_text}",
            rule.severity.upper() if rule.severity else f"{t('groups.rule_severity', lang)}: {any_text}",
            f"🏢 {escape(depots.get(rule.depot_id, '?'))}" if rule.depot_id else f"🏢 {any_text}",
            f"🏷 {escape(rule.vehicle_tag)}" if rule.vehicle_tag else f"🏷 {any_text}",
        ]
    )


async def card_view(session: AsyncSession, group: Group, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    depots = {d.id: d.name for d in await list_depots(session)}
    rules = await svc.group_rules(session, group.id)
    rule_lines = [rule_line(r, depots, lang) for r in rules] or [t("groups.no_rules", lang)]
    text = t(
        "groups.card",
        lang,
        title=escape(group.title or "—"),
        chat_id=group.chat_id,
        topic=f" · topic {group.topic_id}" if group.topic_id else "",
        depot=escape(depots.get(group.depot_id, "—")) if group.depot_id else "—",
        language=LANGUAGE_NAMES.get(group.language, group.language),
        escalation=t("common.yes" if group.is_escalation else "common.no_plain", lang),
        rules="\n".join(rule_lines),
    )
    gid = group.id
    rows = [
        [_btn(t("groups.btn_depot", lang), "depot", gid), _btn(t("groups.btn_language", lang), "lang", gid)],
        [_btn(t("groups.btn_escalation_on" if group.is_escalation else "groups.btn_escalation_off", lang), "esc", gid)],
        [_btn(t("groups.btn_add_rule", lang), "rule", gid)],
    ]
    rows += _rows([_btn(f"🗑 #{r.id}", "rdel", gid, str(r.id)) for r in rules], width=4)
    rows += [[_btn(t("groups.btn_unbind", lang), "unbind", gid)], [_btn(t("common.back", lang), "list")]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(GroupAdmin.filter(F.action == "list"), flags=MANAGE)
async def cb_list(callback: CallbackQuery, callback_data: GroupAdmin, session: AsyncSession, lang: str) -> None:
    text, markup = await list_view(session, int(callback_data.arg or 0), lang)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(GroupAdmin.filter(), flags=MANAGE)
async def cb_group(
    callback: CallbackQuery,
    callback_data: GroupAdmin,
    session: AsyncSession,
    user: User,
    lang: str,
    state: FSMContext,
) -> None:
    action, gid, arg = callback_data.action, callback_data.group_id, callback_data.arg
    try:
        group = await svc.get_group(session, gid)
        note = None
        if action == "depot":
            await _choose_depot(callback, session, gid, lang)
            return
        if action == "setdepot":
            await svc.set_group_depot(session, user, gid, int(arg) or None)
        elif action == "lang":
            buttons = [_btn(LANGUAGE_NAMES[code], "setlang", gid, code) for code in LANGUAGES]
            await callback.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons, [_btn(t("common.back", lang), "view", gid)]])
            )
            await callback.answer()
            return
        elif action == "setlang":
            await svc.set_group_language(session, user, gid, arg)
        elif action == "esc":
            await svc.toggle_escalation(session, user, gid)
        elif action == "rdel":
            await svc.delete_rule(session, user, int(arg))
            note = t("groups.rule_deleted", lang)
        elif action == "unbind":
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[_btn(t("common.yes_delete", lang), "unbindok", gid), _btn(t("common.no", lang), "view", gid)]]
            )
            await callback.message.edit_text(t("groups.confirm_unbind", lang), reply_markup=markup)
            await callback.answer()
            return
        elif action == "unbindok":
            title = await svc.unbind_group(session, user, gid)
            text, markup = await list_view(session, 0, lang)
            await callback.message.edit_text(f"{t('groups.unbound', lang, title=escape(title))}\n\n{text}", reply_markup=markup)
            await callback.answer()
            return
        elif action in {"rule", "wt", "ws", "wd", "wg"}:
            await _rule_wizard(callback, session, user, state, group, action, arg, lang)
            return
        text, markup = await card_view(session, group, lang)
        await callback.message.edit_text(f"{text}\n\n{note}" if note else text, reply_markup=markup)
        await callback.answer()
    except GroupError as error:
        await callback.answer(error.render(lang), show_alert=True)


async def _choose_depot(callback: CallbackQuery, session: AsyncSession, gid: int, lang: str) -> None:
    depots: list[Depot] = await list_depots(session)
    buttons = [_btn(f"🏢 {d.name}", "setdepot", gid, str(d.id)) for d in depots]
    buttons.append(_btn(t("groups.no_depot", lang), "setdepot", gid, "0"))
    rows = _rows(buttons) + [[_btn(t("common.back", lang), "view", gid)]]
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


# --- Routing qoidasi ustasi: tur → severity → depo → tag ---

async def _rule_wizard(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    group: Group,
    action: str,
    arg: str | None,
    lang: str,
) -> None:
    if not await has_permission(user, Permission.MANAGE_ROUTING):
        raise GroupError("access.no_permission")
    gid = group.id
    data = (await state.get_data()).get("rule_wizard") or {}
    if action == "rule" or data.get("group_id") != gid:
        data = {"group_id": gid}

    if action == "wt":
        data["alert_type"] = None if arg == ANY else arg
    elif action == "ws":
        data["severity"] = None if arg == ANY else arg
    elif action == "wd":
        data["depot_id"] = int(arg) or None
    elif action == "wg":
        tags = await distinct_tags(session)
        index = int(arg)
        data["vehicle_tag"] = tags[index] if 0 <= index < len(tags) else None
    await state.update_data(rule_wizard=data)

    back = [_btn(t("common.back", lang), "view", gid)]
    if "alert_type" not in data:
        buttons = [_btn(t("common.any", lang), "wt", gid, ANY)]
        buttons += [_btn(type_label(a.value, lang), "wt", gid, a.value) for a in AlertType if a != AlertType.OTHER]
        await _wizard_step(callback, "groups.wizard_type", _rows(buttons) + [back], lang)
        return
    if "severity" not in data:
        buttons = [_btn(t("common.any", lang), "ws", gid, ANY)] + [_btn(s.value.upper(), "ws", gid, s.value) for s in Severity]
        await _wizard_step(callback, "groups.wizard_severity", _rows(buttons) + [back], lang)
        return
    if "depot_id" not in data:
        depots = await list_depots(session)
        if depots:
            buttons = [_btn(t("common.any", lang), "wd", gid, "0")] + [_btn(f"🏢 {d.name}", "wd", gid, str(d.id)) for d in depots]
            await _wizard_step(callback, "groups.wizard_depot", _rows(buttons) + [back], lang)
            return
        data["depot_id"] = None
    if "vehicle_tag" not in data:
        tags = (await distinct_tags(session))[:MAX_TAGS]
        if tags:
            buttons = [_btn(t("common.any", lang), "wg", gid, "-1")] + [_btn(f"🏷 {tag}", "wg", gid, str(i)) for i, tag in enumerate(tags)]
            await state.update_data(rule_wizard=data)
            await _wizard_step(callback, "groups.wizard_tag", _rows(buttons) + [back], lang)
            return
        data["vehicle_tag"] = None

    await state.update_data(rule_wizard=None)
    rule = await svc.add_rule(
        session,
        user,
        gid,
        alert_type=data["alert_type"],
        severity=data["severity"],
        depot_id=data["depot_id"],
        vehicle_tag=data["vehicle_tag"],
    )
    text, markup = await card_view(session, group, lang)
    await callback.message.edit_text(f"{text}\n\n{t('groups.rule_added', lang, id=rule.id)}", reply_markup=markup)
    await callback.answer()


async def _wizard_step(callback: CallbackQuery, key: str, rows: list[list[InlineKeyboardButton]], lang: str) -> None:
    await callback.message.edit_text(t(key, lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()
