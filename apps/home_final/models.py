from django.db import models
from django.apps import apps
from apps.home.models import *
from apps.wgs_app.models import *
from .models import *
from datetime import timedelta
import re




# Create your models here.


def final_accession_ref_sequence(accession):
    match = re.search(r"(\d+)(?!.*\d)", str(accession or "").strip())
    if not match:
        return None
    return int(match.group(1))


def format_final_age_display(birth_date, specimen_date, age):
    if age in ("", " ", None):
        return ""
    if not birth_date or not specimen_date or specimen_date < birth_date:
        return str(age)

    years = specimen_date.year - birth_date.year
    months = specimen_date.month - birth_date.month
    days = specimen_date.day - birth_date.day

    if days < 0:
        previous_month_last_day = (
            specimen_date.replace(day=1) - timedelta(days=1)
        ).day
        days += previous_month_last_day
        months -= 1

    if months < 0:
        months += 12
        years -= 1

    if years > 0:
        return str(age)

    if months > 0:
        return f"{months}m"
    return f"{days}d"


# for final edit table
class Final_Data(models.Model):

    # ========= META CHOICES =========
    f_Common_Choices = (
        ('n/a','n/a'),
        ('Yes', 'Yes'),
        ('No', 'No'),
    )

    f_Common_pheno = (
        ('n/a','n/a'),
        ('(+)','(+)'),
        ('(-)', '(-)'),
        ('NT', 'NT'),
    )

    f_ServiceTypeChoice = (
        ('n/a','n/a'),
        ('In','In'),
        ('Out','Out')
    )

    f_ReasonChoices = (
        ('n/a','n/a'),
        ('a & d','a & d'),
        ('a','a'),
        ('b','b'),
        ('c','c'),
        ('d','d'),
        ('e','e'),
        ('o','o')
    )

    Growth_Choice = (
        ('n/a','n/a'),
        ('light growth','light growth'),
        ('light to moderate growth','light to moderate growth'),
        ('heavy growth', 'heavy growth'),
        ('moderately heavy growth', 'moderately heavy growth'),
        ('positive growth', 'positive growth'),
        ('no growth','no growth'),


    )

    Gender_Choice = (
        ('n/a','n/a'),
        ('f','f'),
        ('m','m'),
     
    )
   
    # ========= BATCH / META =========
    f_bat_seq = models.PositiveIntegerField(null=True, blank=True)
    f_Batch_id = models.ForeignKey(Batch_Table, on_delete=models.CASCADE, related_name='final_isolates', null=True,)
    f_Hide=models.BooleanField(default=False)
    f_Batch_Code = models.CharField(max_length=255, blank=True, default="")
    f_Batch_Name = models.CharField(max_length=255, blank=True, default="")
    f_RefNo = models.CharField(max_length=20, blank=True, default="")
    f_BatchNo = models.CharField(max_length=255, blank=True, default="")
    f_Total_batch = models.CharField(max_length=100, blank=True, default="")

    f_AccessionNo = models.CharField(max_length=255, unique=True)
    # Flag only: Raw edits never silently overwrite approved/edited Final data.
    raw_updated_after_copy = models.BooleanField(default=False)
    raw_updated_after_copy_at = models.DateTimeField(null=True, blank=True)
    raw_updated_after_copy_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="final_copies_with_raw_updates",
    )
    raw_updated_after_copy_fields = models.JSONField(default=list, blank=True)

    f_AccessionNoGen = models.CharField(max_length=100, blank=True, default="")

    f_Date_of_Entry = models.DateTimeField(auto_now_add=True)
    f_Date_Modified = models.DateTimeField(auto_now=True)

    f_SiteCode = models.CharField(max_length=255, blank=True, default="")
    f_Site_Name = models.CharField(max_length=255, blank=True, default="")
    f_Referral_Date = models.DateField(null=True, blank=True)

    # ========= PATIENT =========
    f_Patient_ID = models.CharField(max_length=255, blank=True, default="")
    f_First_Name = models.CharField(max_length=255, blank=True, default="")
    f_Mid_Name = models.CharField(max_length=255, blank=True, default="")
    f_Last_Name = models.CharField(max_length=255, blank=True, default="")

    f_Date_Birth = models.DateField(null=True, blank=True)
    f_Age = models.IntegerField(
    blank=True,
    null=True,
    validators=[MinValueValidator(0), MaxValueValidator(120)]
    )
    f_Age_Display = models.CharField(max_length=20, blank=True, default="")
    f_Emerging_Flag_Age=models.BooleanField(default=False)
    f_Sex = models.CharField(max_length=255, blank=True, choices=Gender_Choice, default="n/a")

    f_Date_Admis = models.DateField(null=True, blank=True)
    f_Nosocomial=models.CharField(max_length=255, choices=f_Common_Choices, default="n/a")
    f_Diagnosis=models.CharField(max_length=255, blank=True,)
    f_Diagnosis_ICD10=models.CharField(max_length=255, blank=True,)
    f_Ward=models.CharField(max_length=255, blank=True,)
    f_Ward_Type = models.CharField(max_length=255, blank=True,)
    f_Service_Type = models.CharField(
        max_length=255,
        choices=f_ServiceTypeChoice,
        default="n/a"
    )

    # ========= SPECIMEN =========
    f_Spec_Num = models.CharField(max_length=255, blank=True, default="")
    f_Spec_Date = models.DateField(null=True, blank=True)
    f_Spec_Type = models.ForeignKey(
    SpecimenTypeModel,
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="final_specimen_type_entries"
)


    f_Reason = models.TextField(
        max_length=255,
        choices=f_ReasonChoices,
        default="n/a"
    )
    f_Spec_Emerging = models.BooleanField(default=False)
    f_Growth = models.CharField(max_length=255, blank=True, default="", choices=Growth_Choice)
    f_Urine_ColCt = models.CharField(max_length=255, blank=True, default="")

    # ========= PHENOTYPE =========
    f_ampC = models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ESBL = models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_CARB = models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_MBL = models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_BL = models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_MR = models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_mecA = models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ICR = models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_OtherResMech = models.CharField(max_length=255, blank=True, default="")

    # ========= ORGANISM =========
    f_Site_Pre = models.CharField(max_length=255, blank=True, null=True, default="")
    f_Site_Pre_ed = models.TextField(blank=True, default="")
    f_Site_Org = models.CharField(max_length=255, blank=True, default="")
    f_Site_OrgName = models.CharField(max_length=255, blank=True, default="")
    f_Site_Pos = models.CharField(max_length=255, blank=True, null=True, default="")
    f_Site_Pos_ed = models.TextField(blank=True, default="")
    f_Comments = models.TextField(blank=True, default="")

    # ========= ARSRL =========
    f_ars_ampC=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_ESBL=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_CARB=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_ECIM=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_MCIM=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_EC_MCIM=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_MBL=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_BL=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_MR=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_mecA=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_ICR=models.CharField(max_length=255, choices=f_Common_pheno, default="n/a")
    f_ars_Pre=models.CharField( max_length=255, blank=True, null=True, default='')
    f_ars_Pre_ed=models.TextField(blank=True, default="")
    f_ars_Post=models.CharField(max_length=255, blank=True, null=True, default='')
    f_ars_Post_ed=models.TextField(blank=True, default="")
    f_ars_OrgCode=models.CharField(max_length=255, blank=True, default="")
    f_ars_OrgName=models.CharField(max_length=255, blank=True,)
    f_ars_ct_ctl=models.CharField(max_length=255, blank=True,)
    f_ars_tz_tzl=models.CharField(max_length=255, blank=True,)
    f_ars_cn_cni=models.CharField(max_length=255, blank=True,)
    f_ars_ip_ipi=models.CharField(max_length=255, blank=True,)
    f_ars_reco_Code=models.CharField(max_length=255, blank=True, null=True)
    f_ars_description = models.TextField(blank=True, null=True)
    f_ars_reco=models.TextField(blank=True, null=True)

    # ========= SIGNATORIES =========
    f_arsp_Encoder = models.CharField(max_length=255, blank=True, default="")
    f_arsp_Enc_Lic = models.CharField(max_length=100, blank=True, default="")
    f_arsp_Checker = models.CharField(max_length=255, blank=True, default="")
    f_arsp_Chec_Lic = models.CharField(max_length=100, blank=True, default="")
    f_arsp_Verifier = models.CharField(max_length=255, blank=True, default="")
    f_arsp_Ver_Lic = models.CharField(max_length=100, blank=True, default="")
    f_arsp_LabManager = models.CharField(max_length=255, blank=True, default="")
    f_arsp_Lab_Lic = models.CharField(max_length=100, blank=True, default="")
    f_arsp_Head = models.CharField(max_length=255, blank=True, default="")
    f_arsp_Head_Lic = models.CharField(max_length=100, blank=True, default="")
    f_Date_Accomplished_ARSP = models.DateField(null=True, blank=True)

    # ========= EXTRA =========
    f_x_mrse = models.CharField(max_length=255, blank=True, default="")
    f_x_mrsamrse = models.CharField(max_length=255, blank=True, default="")
    f_x_entbac = models.CharField(max_length=255, blank=True, default="")
    f_edta = models.CharField(max_length=255, blank=True, default="")


    def save(self, *args, **kwargs):

        derived_seq = final_accession_ref_sequence(self.f_AccessionNo)
        if derived_seq is not None:
            self.f_bat_seq = derived_seq
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "f_bat_seq" not in update_fields:
                kwargs["update_fields"] = list(update_fields) + ["f_bat_seq"]

        self.f_arsp_Encoder     = self.f_arsp_Encoder or ""
        self.f_arsp_Enc_Lic     = self.f_arsp_Enc_Lic or ""
        self.f_arsp_Checker     = self.f_arsp_Checker or ""
        self.f_arsp_Chec_Lic    = self.f_arsp_Chec_Lic or ""
        self.f_arsp_Verifier    = self.f_arsp_Verifier or ""
        self.f_arsp_Ver_Lic     = self.f_arsp_Ver_Lic or ""
        self.f_arsp_LabManager  = self.f_arsp_LabManager or ""
        self.f_arsp_Lab_Lic     = self.f_arsp_Lab_Lic or ""
        self.f_arsp_Head        = self.f_arsp_Head or ""
        self.f_arsp_Head_Lic    = self.f_arsp_Head_Lic or ""

        self.f_Batch_Code       = self.f_Batch_Code or ""
        self.f_Batch_Name       = self.f_Batch_Name or ""
        self.f_RefNo            = self.f_RefNo or ""
        self.f_BatchNo          = self.f_BatchNo or ""
        self.f_Total_batch      = self.f_Total_batch or ""

        self.f_Site_Pre         = self.f_Site_Pre or ""
        self.f_Site_Pre_ed      = self.f_Site_Pre_ed or ""
        self.f_Site_Pos         = self.f_Site_Pos or ""
        self.f_Site_Pos_ed      = self.f_Site_Pos_ed or ""
        self.f_ars_Pre          = self.f_ars_Pre or ""
        self.f_ars_Pre_ed       = self.f_ars_Pre_ed or ""
        self.f_ars_Post         = self.f_ars_Post or ""
        self.f_ars_Post_ed      = self.f_ars_Post_ed or ""

        
        self.f_Site_Org         = self.f_Site_Org or ""
        self.f_Site_OrgName     = self.f_Site_OrgName or ""
        self.f_ars_OrgCode      = self.f_ars_OrgCode or ""
        self.f_ars_OrgName      = self.f_ars_OrgName or ""


        self.f_Comments         = self.f_Comments or ""
        self.f_ars_description = self.f_ars_description or ""
        self.f_ars_reco         = self.f_ars_reco or ""


        self.f_First_Name = self.f_First_Name or ""
        self.f_Mid_Name   = self.f_Mid_Name or ""
        self.f_Last_Name  = self.f_Last_Name or ""

        site = None
        if self.f_SiteCode:
            site = SiteData.objects.filter(SiteCode=self.f_SiteCode).first()
        if site:
                self.f_Site_Name = site.SiteName

        if self.f_Age in ("", " ", None):
            self.f_Age = None

        self.f_Age_Display = format_final_age_display(
            self.f_Date_Birth,
            self.f_Spec_Date,
            self.f_Age,
        )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "f_Age_Display" not in update_fields:
            kwargs["update_fields"] = list(update_fields) + ["f_Age_Display"]
    

        super().save(*args, **kwargs)

    def __str__(self):
        return self.f_AccessionNo


    class Meta:
        db_table = "Final_Data"
        constraints = [
            models.UniqueConstraint(
                fields=['f_AccessionNo', 'f_Batch_Code'],
                name='unique_final_accession_batch'
            ),
        
        ]






#for final antibiotic test entries
class Final_AntibioticEntry(models.Model):
#  links to main and breakpoints table
    
    ab_idNum_f_referred = models.ForeignKey(
    Final_Data,
    on_delete=models.CASCADE,
    related_name='final_entries'
)

    ab_Site_Org = models.CharField(max_length=100, blank=True, null=True)
    ab_Ret_Org = models.CharField(max_length=100, blank=True, null=True)
    
    ab_Org_Flag = models.BooleanField(default=False)
    ab_Abx_Flag = models.BooleanField(default=False)
    ab_Abx_Phenotype = models.CharField(max_length=20, blank=True, default="")
    ab_Abx_Phenotype_Other = models.CharField(max_length=20, blank=True, default="")

    ab_Disk_Abx = models.BooleanField(default=False)
    ab_Ret_Disk_Abx= models.BooleanField(default=False)
    ab_AccessionNo= models.CharField(max_length=30, blank=True, null=True)
    ab_RefNo = models.CharField(max_length=100, blank=True, null=True)
    ab_breakpoints_id = models.ManyToManyField('home.BreakpointsTable', max_length=6)

    ab_eme_specimen = models.CharField(max_length=100, blank=True, null=True)

    ab_Antibiotic = models.CharField(max_length=255, blank=True, null=True)
    ab_Abx_code= models.CharField(max_length=25, blank=True, null=True)
    ab_Abx=models.CharField(max_length=100, blank=True, null=True)

    #sentinel site results
    ab_Disk_value = models.PositiveSmallIntegerField(null=True, blank=True)
    ab_Disk_RIS = models.CharField(max_length=4, blank=True) 
    ab_Disk_enRIS = models.CharField(max_length=4, blank=True, default='') 
    
    ab_MIC_operand=models.CharField(max_length=4, blank=True, null=True, default='')
    ab_MIC_value = models.DecimalField(max_digits=7, decimal_places=3, blank=True, null=True)

    ab_MIC_RIS = models.CharField(max_length=4, blank=True)
    ab_MIC_enRIS = models.CharField(max_length=4, blank=True, default='')
    
    ab_AlertMIC = models.BooleanField(default=False)
    ab_Alert_val = models.CharField(max_length=30, blank=True, null=True, default='')

    ab_R_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    ab_I_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    ab_SDD_breakpoint = models.CharField(max_length=10, blank=True, null=True)  
    ab_S_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    
    #arsrl results
    ab_Retest_Antibiotic = models.CharField(max_length=255, blank=True, null=True)
    ab_Retest_Abx_code = models.CharField(max_length=100, blank=True, null=True)
    ab_Retest_Abx = models.CharField(max_length=100, blank=True, null=True)
    
    ab_Retest_DiskValue = models.IntegerField(blank=True, null=True)
    ab_Retest_Disk_RIS = models.CharField(max_length=4, blank=True)
    ab_Retest_Disk_enRIS = models.CharField(max_length=4, blank=True, default='')
    
    ab_Retest_MIC_operand=models.CharField(max_length=4, blank=True, null=True, default='')
    ab_Retest_MICValue = models.DecimalField(max_digits=7, decimal_places=3, blank=True, null=True)

    ab_Retest_MIC_RIS = models.CharField(max_length=4, blank=True)
    ab_Retest_MIC_enRIS = models.CharField(max_length=4, blank=True, default='')    
    
    ab_Retest_AlertMIC = models.BooleanField(default=False)
    ab_Retest_Alert_val = models.CharField(max_length=30, blank=True, null=True, default='')
    ab_Ret_R_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    ab_Ret_I_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    ab_Ret_SDD_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    ab_Ret_S_breakpoint = models.CharField(max_length=10, blank=True, null=True)    
    ab_Date_uploaded_fd = models.DateField(auto_now_add=True)

    ab_MICJoined = models.CharField(max_length=7, blank=True, null=True)
    
    def __str__(self):
        return ", ".join([abx.Whonet_Abx for abx in self.ab_breakpoints_id.all()]) 


    def save(self, *args, **kwargs):
        if self.ab_Disk_enRIS:
            self.ab_Disk_enRIS = self.ab_Disk_enRIS.upper()

        if self.ab_MIC_enRIS:
            self.ab_MIC_enRIS = self.ab_MIC_enRIS.upper()
        
        if self.ab_Retest_Disk_enRIS:
            self.ab_Retest_Disk_enRIS = self.ab_Retest_Disk_enRIS.upper()

        if self.ab_Retest_MIC_enRIS:
            self.ab_Retest_MIC_enRIS = self.ab_Retest_MIC_enRIS.upper()

        super().save(*args, **kwargs)  # Save the instance first
        

    class Meta:
        db_table = "FinalAntibioticEntry"


class FinalAntibiotic_upload(models.Model):
    FinalAntibioticFile = models.FileField(upload_to='uploads/final/antibiotic/', null=True, blank=True)

    class Meta:
        db_table ="FinalAntibiotic_upload"






class Emerging_Table(models.Model):
    eme_primary_key = models.ForeignKey(
        Final_Data,
        on_delete=models.CASCADE,
        null=True,
        related_name='eme_listing'
    )

    eme_Spec_Flag = models.BooleanField(default=True)
    eme_Site_Code = models.CharField(max_length=200, blank=True, null=True)
    eme_Accession = models.CharField(max_length=200, blank=True, null=True)
    eme_ReferralDate = models.CharField(max_length=200, blank=True, null=True)
    eme_DateAdmis = models.CharField(max_length=200, blank=True, null=True)
    eme_Diagnosis = models.CharField(max_length=200, blank=True, null=True)
    eme_Diag_ICD = models.CharField(max_length=200, blank=True, null=True)
    eme_ars_Org = models.CharField(max_length=200, blank=True, null=True)

    eme_org_Flag = models.BooleanField(default=False)
    eme_abx_Flag = models.BooleanField(default=False)

    eme_abx_code_pheno = models.CharField(max_length=200, blank=True, null=True)
    eme_abx_Phenotype = models.CharField(max_length=200, blank=True, null=True)

    eme_ars_Pre = models.CharField(max_length=200, blank=True, null=True)
    eme_ars_Post = models.CharField(max_length=200, blank=True, null=True)

    eme_org_Grp = models.CharField(max_length=200, blank=True, null=True)
    eme_org_Genus = models.CharField(max_length=200, blank=True, null=True)

    eme_spec_Num = models.CharField(max_length=200, blank=True, null=True)
    eme_spec_Date = models.CharField(max_length=200, blank=True, null=True)
    eme_spec_Type = models.CharField(max_length=200, blank=True, null=True)

    class Meta:
        db_table = "Emerging_Table"

    def save(self, *args, **kwargs):
        # Guard: model → code
        if isinstance(self.eme_spec_Type, SpecimenTypeModel):
            self.eme_spec_Type = self.eme_spec_Type.Specimen_code

        super().save(*args, **kwargs)


    @classmethod
    def fully_emerging(cls):
        from django.db.models import Q

        return cls.objects.filter(
            eme_Spec_Flag=True,
            eme_org_Flag=True,
            eme_abx_Flag=True,
        ).filter(
            eme_spec_Type__isnull=False,
            eme_ars_Org__isnull=False,
            eme_abx_code_pheno__isnull=False,
        ).exclude(
            Q(eme_spec_Type="") |
            Q(eme_ars_Org="") |
            Q(eme_abx_code_pheno="")
        )



class Classification_Table(models.Model):
    Class_idNumReferred = models.ForeignKey(Final_Data, on_delete=models.CASCADE, related_name='class_entry')
    Class_AccessionNo = models.CharField(max_length=200, blank=True, null=True)
    Class_Chk_Emerging = models.BooleanField(default=False)
    Class_Chk_Structured = models.BooleanField(default=False)
    Class_Chk_Satscan = models.BooleanField(default=False)
    Class_Chk_Serotyping = models.BooleanField(default=False)
    Class_Chk_GHRU_all = models.BooleanField(default=False)
    Class_Chk_GHRU_Neo = models.BooleanField(default=False)
    Class_Chk_EGASP = models.BooleanField(default=False)
    Class_Chk_Tricycle = models.BooleanField(default=False)
    Class_Chk_Pulsenet = models.BooleanField(default=False)
    Class_Chk_Tulip = models.BooleanField(default=False)

    
    class Meta:
        db_table = "Classification_Table"
        constraints = [
            models.UniqueConstraint(
                fields=["Class_idNumReferred"],
                name="unique_classification_per_isolate"
            )
        ]




### CONCORDANCE ANALYSIS TABLES
class ConcordanceReport(models.Model):

    batch = models.ForeignKey(
        Batch_Table,
        on_delete=models.CASCADE,
        related_name="concordance_reports",
        null=True,
        blank=True
    )

    final_data = models.ForeignKey(
        Final_Data,
        on_delete=models.CASCADE,
        related_name="concordance_reports",
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    total_isolates = models.IntegerField(default=0)
    total_pairs = models.IntegerField(default=0)

    concordant_pairs = models.IntegerField(default=0)
    vmd = models.IntegerField(default=0)
    md = models.IntegerField(default=0)
    minor = models.IntegerField(default=0)

    total_deviation = models.IntegerField(default=0)
    critical_deviation = models.IntegerField(default=0)

    ast_concordance_rate = models.FloatField(default=0)
    critical_deviation_rate = models.FloatField(default=0)
    total_deviation_rate = models.FloatField(default=0)

    genus_match = models.IntegerField(default=0)
    species_match = models.IntegerField(default=0)
    genus_rate = models.FloatField(default=0)
    species_rate = models.FloatField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["-created_at"], name="concordance_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["batch"],
                condition=models.Q(final_data__isnull=True),
                name="unique_batch_concordance"
            ),
            models.UniqueConstraint(
                fields=["final_data"],
                condition=models.Q(final_data__isnull=False),
                name="unique_accession_concordance"
            ),
        ]


class ConcordanceDetail(models.Model):

    report = models.ForeignKey(
        ConcordanceReport,
        on_delete=models.CASCADE,
        related_name="details"
    )

    accession_no = models.CharField(max_length=100)
    isolate_id = models.IntegerField()

    organism = models.CharField(max_length=255)
    antibiotic = models.CharField(max_length=255)

    site_ris = models.CharField(max_length=3)
    ars_ris = models.CharField(max_length=3)

    deviation_code = models.CharField(max_length=1, null=True, blank=True)
    is_discordant = models.BooleanField(default=False)

    genus_con = models.CharField(max_length=1, null=True, blank=True)
    species_con = models.CharField(max_length=1, null=True, blank=True)

    def __str__(self):
        return f"{self.accession_no} - {self.antibiotic} ({self.deviation_code})"



class ConcordanceOptions(models.Model):
    report = models.OneToOneField(
        ConcordanceReport,
        on_delete=models.CASCADE,
        related_name="options",
        null=True,
        blank=True
    )

    prioritize_mic_site = models.BooleanField(default=False)
    prioritize_disk_site = models.BooleanField(default=False)
    no_serotyping = models.BooleanField(default=False)
    applied_org = models.CharField(max_length=255, blank=True, default="")
    applied_org_grp = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Concordance Options"


