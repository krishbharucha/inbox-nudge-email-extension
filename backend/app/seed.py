from datetime import timedelta

from sqlalchemy import select

from .models import ConnectedAccount, EmailThread, RecipientPreferences, Reminder, User, now


def seed_demo(db):
    if db.scalar(select(User).where(User.id == "alex")):
        return
    people = [
        ("alex", "alex.morgan@example.com", "Alex Morgan"),
        ("maya", "maya.chen@example.com", "Maya Chen"),
        ("jordan", "jordan.lee@example.com", "Jordan Lee"),
        ("sophie", "sophie.wilson@example.com", "Sophie Wilson"),
    ]
    for id_, email, name in people:
        db.add(User(id=id_, email=email, display_name=name, is_demo=True))
    db.flush()
    for id_, email, _ in people:
        db.add(ConnectedAccount(user_id=id_, provider_account_id=email, provider="demo"))
        db.add(RecipientPreferences(user_id=id_))
    db.flush()
    threads = [
        (
            "design",
            "maya",
            "alex",
            "Website refresh · final design review",
            "Hey Alex! Sharing the updated designs for the website refresh. Would love your thoughts on the new direction before we hand things over to development.",
            3,
            False,
        ),
        (
            "coffee",
            "jordan",
            "alex",
            "A quick coffee next week?",
            "Hey Alex, it’s been a while! I’ll be in your neighborhood next week. Would be great to catch up over coffee if you’re around.",
            5,
            False,
        ),
        (
            "proposal",
            "sophie",
            "alex",
            "Partnership proposal for Q4",
            "Hi Alex, following up on our conversation. I’ve attached an outline of how we could work together this quarter. Let me know what you think!",
            2,
            False,
        ),
        (
            "launch",
            "alex",
            "maya",
            "Launch checklist — a few final details",
            "Hi Maya, here’s the final checklist for our upcoming launch. Could you take a look at the timeline and share any feedback?",
            4,
            False,
        ),
        (
            "recruiter",
            "alex",
            None,
            "Following up on the product designer role",
            "Hi Taylor, thank you again for the conversation last week. I’m excited about the opportunity and would love to hear about next steps.",
            6,
            False,
        ),
        (
            "replied",
            "alex",
            "jordan",
            "Team offsite ideas",
            "A few ideas for our next team offsite. Thanks for already getting back to me!",
            1,
            True,
        ),
    ]
    emails = {p[0]: p[1] for p in people}
    for id_, sender, recipient, subject, preview, days, replied in threads:
        db.add(
            EmailThread(
                id=id_,
                provider_thread_id=f"{sender}-mailbox-{id_}",
                recipient_provider_thread_id=f"{recipient}-mailbox-{id_}" if recipient else None,
                sender_user_id=sender,
                recipient_email=emails.get(recipient, "taylor.reed@example.com"),
                subject=subject,
                preview=preview,
                last_message_at=now() - timedelta(days=days),
                has_reply=replied,
                verification_source="demo",
            )
        )
    db.flush()
    for i, (thread_id, sender, hours) in enumerate(
        [("design", "maya", 1), ("coffee", "jordan", 3), ("proposal", "sophie", 5)]
    ):
        db.add(
            Reminder(
                id=f"welcome-{i}",
                thread_id=thread_id,
                sender_user_id=sender,
                recipient_user_id="alex",
                idempotency_key=f"seed-reminder-{i}",
                status="delivered",
                created_at=now() - timedelta(hours=hours),
                delivered_at=now() - timedelta(hours=hours),
            )
        )
    db.commit()
