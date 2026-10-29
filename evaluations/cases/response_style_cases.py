"""Synthetic survey types for contextual style checks, without expected reply scripts."""
from dataclasses import dataclass

from voice_agent.conversation import ConversationManager, Question, Questionnaire


@dataclass(frozen=True)
class StyleCase:
    name: str
    question: Question
    customer: str
    expected_action: str = "stay"
    expected_status: str = "needs_clarification"
    expected_value: str | None = None
    previous_reply: str = ""

    def manager(self):
        manager = ConversationManager(Questionnaire(
            questions=(self.question,),
            authorized_information="الجهة المنفذة للاستبيان هي شركة المدار. المشاركة اختيارية.",
        ))
        if self.previous_reply:
            manager.history.extend([
                {"role": "user", "content": "التجربة جيدة"},
                {"role": "assistant", "content": self.previous_reply},
            ])
        return manager


CASES = (
    StyleCase("positive_feedback_five_point", Question(
        id="experience", text="كيف تقيّم تجربتك من واحد إلى خمسة؟", kind="rating",
        minimum=1, maximum=5, relevance="تقييم رقمي صريح للتجربة العامة."), "جيدة الصراحة"),
    StyleCase("negative_feedback_ten_point", Question(
        id="delivery", text="كيف تقيّم تجربة التوصيل من صفر إلى عشرة؟", kind="rating",
        minimum=0, maximum=10, relevance="تقييم تجربة التوصيل برقم يختاره العميل."),
        "التوصيل تأخر واجد"),
    StyleCase("repeated_clarification_seven_point", Question(
        id="visit", text="كيف تقيّم زيارتك من واحد إلى سبعة؟", kind="rating",
        minimum=1, maximum=7, optional=True, relevance="تقييم رقمي لتجربة الزيارة."),
        "قلت لك زينة، ليش تعيد السؤال؟", previous_reply="وكم تعطي الزيارة من واحد إلى سبعة؟"),
    StyleCase("ambiguous_contact_choice", Question(
        id="contact", text="أي قناة تفضّل للتواصل؟", kind="choice",
        choices=("الهاتف", "البريد الإلكتروني"), relevance="اختيار قناة التواصل المفضلة."),
        "مب متأكد أي واحد أختار"),
    StyleCase("translated_contact_choice", Question(
        id="contact", text="أي قناة تفضّل للتواصل؟", kind="choice",
        choices=("الهاتف", "البريد الإلكتروني"), relevance="اختيار قناة التواصل المفضلة."),
        "I prefer email", expected_action="next_question", expected_status="valid",
        expected_value="البريد الإلكتروني"),
    StyleCase("brief_negative_text", Question(
        id="app_feedback", text="ما ملاحظاتك عن التطبيق؟", kind="text",
        relevance="ملاحظة عن استخدام التطبيق أو أدائه."), "التطبيق يعلق",
        expected_action="next_question", expected_status="valid"),
    StyleCase("conditional_details_missing", Question(
        id="registration", text="هل واجهت صعوبة في التسجيل؟ إذا نعم، ما هي؟",
        kind="conditional_text", insufficient_answers=("نعم",),
        relevance="تفاصيل صعوبة تخص التسجيل أو نفي وجود صعوبة."), "نعم"),
    StyleCase("consent_company_name", Question(
        id="consent", text="هل تسمح لنا نبدأ الاستبيان؟", kind="consent",
        choices=("نعم", "لا"), relevance="موافقة صريحة على المشاركة."),
        "من أي شركة قلت لي؟", expected_status="not_answered"),
)
