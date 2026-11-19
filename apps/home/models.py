from django.utils import timezone
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.validators import EmailValidator
from phonenumber_field.modelfields import PhoneNumberField
from django.contrib.auth.models import User
from apps.home.utils import working_days
from datetime import timedelta
import re

# Create your models here.


def accession_ref_sequence(accession):
    """
    Return the accession reference suffix as an integer.

    Examples:
    - 25ARS_APM0278 -> 278
    - 25ARS_APM0009 -> 9
    """
    match = re.search(r"(\d+)(?!.*\d)", str(accession or "").strip())
    if not match:
        return None
    return int(match.group(1))


def format_age_display(birth_date, specimen_date, age):
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


class Batch_Table(models.Model):

    Batch_Status = (
        ('n/a',''),
        ('Encoding','Encoding'),
        ('First Draft', '1st Draft'),
        ('Second Draft', '2nd Draft'),
        ('Third Draft', '3rd Draft'),
        ('Verification','Verification'),
        ('Other','Other'),
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_batches",
    )
    bat_SiteCode = models.CharField(max_length=255, blank=True, default='')
    bat_Seq_No = models.IntegerField(null=True, blank=True)
    bat_Site_Name = models.CharField(max_length=255, blank=True)
    bat_Site_NameGen = models.CharField(max_length=255, blank=True)
    bat_Batch_Name = models.CharField(max_length=255, blank=True)
    bat_Batch_Code = models.CharField(max_length=255, blank=True)


    bat_RefNo = models.CharField(null=True, blank=True)
    bat_BatchNo = models.CharField(max_length=255, blank=True)
    bat_Total_batch = models.CharField(max_length=100, blank=True)
    bat_AccessionNo = models.CharField(max_length=255, blank=True)
    bat_AccessionNoGen = models.CharField(max_length=100, blank=True)
    bat_Default_Year = models.DateField(null=True, blank=True)
    bat_Referral_Date = models.DateField(null=True, blank=True)

    bat_Status = models.CharField(max_length=100, choices=Batch_Status, default='')

    bat_Encoder = models.CharField(max_length=255, blank=True, default='')
    bat_Enc_Lic = models.CharField(max_length=100, blank=True, default='')

    bat_Checker = models.CharField(max_length=255, blank=True, default='')
    bat_Chec_Lic = models.CharField(max_length=100, blank=True, default='')

    bat_Verifier = models.CharField(max_length=255, blank=True, default='')
    bat_Ver_Lic = models.CharField(max_length=100, blank=True, default='')

    bat_LabManager = models.CharField(max_length=255, blank=True, default='')
    bat_Lab_Lic = models.CharField(max_length=100, blank=True, default='')

    bat_Head = models.CharField(max_length=255, blank=True, default='')
    bat_Head_Lic = models.CharField(max_length=100, blank=True, default='')

    bat_Date_of_Entry = models.DateTimeField(auto_now_add=True)
    bat_Date_Accomplished = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "Batch_Table"


        def __init__(self, *args, **kwargs):
                editing = kwargs.pop("editing", False)
                super().__init__(*args, **kwargs)

                if editing:
                    LOCKED_FIELDS = [
                        "bat_SiteCode",
                        "bat_Referral_Date",
                        "bat_BatchNo",
                        "bat_Total_batch",
                        "bat_RefNo",
                        "bat_Site_NameGen",
                        "bat_Batch_Name",
                    ]

                    for field in LOCKED_FIELDS:
                        self.fields[field].disabled = True


class Referred_Data(models.Model):
    Common_Choices = (
        ('n/a','n/a'),
        ('Yes', 'Yes'),
        ('No', 'No'),
    )

    Common_pheno = (
        ('n/a','n/a'),
        ('(+)','(+)'),
        ('(-)', '(-)'),
        ('NT', 'NT'),
    )
    SexatbirthChoice=(
        ('n/a','n/a'),
        ('Male', 'Male'),
        ('Female', 'Female')
    )

    ServiceTypeChoice=(
        ('n/a','n/a'),
        ('In','In'),
        ('Out','Out')
        
    )
    ReasonChoices=(
        ('n/a','n/a'),
        ('a & d','a & d'),
        ('a','a'),
        ('b','b'),
        ('c','c'),
        ('d','d'),
        ('e','e'),
        ('o','o')
    )

    Status_Choice = (
        ('n/a','n/a'),
        ('Encoding','Encoding'),
        ('First Draft', '1st Draft'),
        ('Second Draft', '2nd Draft'),
        ('Third Draft', '3rd Draft'),
        ('Verification','Verification'),
        ('Other','Other'),

    )

    Growth_Choice = (
        ('n/a','n/a'),
        ('light growth','light growth'),
        ('light to moderate growth','light to moderate growth'),
        ('moderate growth', 'moderate growth'),
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
   
   
    #isolates
    bat_seq = models.PositiveIntegerField(null=True, blank=True, help_text="Auto sequence number per batch")
    Batch_id = models.ForeignKey(Batch_Table, on_delete=models.CASCADE, related_name='Batch_isolates', null=True,)
    Hide=models.BooleanField(default=False)
    Copy_data=models.BooleanField(default=False)
    Batch_Name=models.CharField(max_length=255, blank=True,)
    Batch_Code = models.CharField(max_length=255, blank=True)
    Date_of_Entry =models.DateTimeField(auto_now_add=True)
    Date_Modified =models.DateTimeField(auto_now=True)
    RefNo = models.CharField(max_length=20, blank=True, null=True)
    BatchNo=models.CharField(max_length=255, blank=True,)
    Total_batch=models.CharField(max_length=100, blank=True,)
    AccessionNo=models.CharField(max_length=255, blank=True, unique=True)
    AccessionNoGen=models.CharField(max_length=100, blank=True)
    Default_Year=models.DateField(null=True, blank=True)
    SiteCode=models.CharField(max_length=255, blank=True,) #
    Site_Name=models.CharField(max_length=255, blank=True,) #
    # Site_NameGen = models.CharField(max_length=255, blank=True,) #
    Referral_Date=models.DateField(null=True, blank=True)
    #Patient Information
    Patient_ID=models.CharField(max_length=255, blank=True,)
    First_Name=models.CharField(max_length=255, blank=True,)
    Mid_Name=models.CharField(max_length=255, blank=True,)
    Last_Name=models.CharField(max_length=255, blank=True,)
    Date_Birth=models.DateField(null=True, blank=True)
    Age = models.IntegerField(
    blank=True,
    null=True,
    validators=[MinValueValidator(0), MaxValueValidator(120)]
    )
    Age_Display = models.CharField(max_length=20, blank=True, default="")
    Sex=models.CharField(max_length=255, blank=True, choices=Gender_Choice, default="n/a")
    Date_Admis=models.DateField(null=True, blank=True)
    Nosocomial=models.CharField(max_length=255, choices=Common_Choices, default="n/a")
    Diagnosis=models.CharField(max_length=255, blank=True,)
    Diagnosis_ICD10=models.CharField(max_length=255, blank=True,)
    Ward=models.CharField(max_length=255, blank=True,)
    Ward_Type = models.CharField(max_length=255, blank=True,)
    Service_Type=models.CharField(max_length=255, choices=ServiceTypeChoice, default="n/a")
    #Isolate Information
    Spec_Num=models.CharField(max_length=255, blank=True,)
    Spec_Date=models.DateField(null=True, blank=True)
    # Spec_Type=models.CharField(max_length=255, blank=True, null=True)
    Spec_Type = models.ForeignKey("SpecimenTypeModel", on_delete=models.SET_NULL, null=True,blank=True, related_name='specimen_type_entries')
    Reason=models.TextField(max_length=255, choices=ReasonChoices, default="n/a")
    Growth=models.CharField(max_length=255, blank=True, choices=Growth_Choice, default="n/a")
    Urine_ColCt=models.CharField(max_length=255, blank=True,)
    #Phenotypic Results
    ampC=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ESBL=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    CARB=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    MBL=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    BL=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    MR=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    mecA=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ICR=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    OtherResMech=models.CharField(max_length=255, blank=True)
    #Organism Result
    Site_Pre=models.CharField(max_length=255, blank=True, default="")
    Site_Pre_ed=models.TextField(blank=True, default="")
    Site_Org=models.CharField(max_length=255, blank=True, default="")
    Site_OrgName=models.CharField(max_length=255, blank=True, null=True)
    Site_Pos=models.CharField(max_length=255, blank=True, default="")
    Site_Pos_ed=models.TextField(blank=True, default="")
    Comments=models.TextField(blank=True, null=True)
    
    #ARSRL Sty Results
    ars_ampC=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_ESBL=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_CARB=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_ECIM=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_MCIM=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_EC_MCIM=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_MBL=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_BL=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_MR=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_mecA=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_ICR=models.CharField(max_length=255, choices=Common_pheno, default="n/a")
    ars_Pre=models.CharField( max_length=255, blank=True, default="")
    ars_Pre_ed=models.TextField(blank=True, default="")
    ars_Post=models.CharField(max_length=255, blank=True, default="")
    ars_Post_ed=models.TextField(blank=True, default="")
    ars_OrgCode=models.CharField(max_length=255, blank=True, default="")
    ars_OrgName=models.CharField(max_length=255, blank=True,)
    ars_ct_ctl=models.CharField(max_length=255, blank=True,)
    ars_tz_tzl=models.CharField(max_length=255, blank=True,)
    ars_cn_cni=models.CharField(max_length=255, blank=True,)
    ars_ip_ipi=models.CharField(max_length=255, blank=True,)
    ars_reco_Code=models.CharField(max_length=255, blank=True, null=True)
    ars_description = models.TextField(blank=True, null=True)
    ars_reco=models.TextField(blank=True, null=True, default='')
    
    #Batch Table Data
    SiteName=models.CharField(max_length=255, blank=True,)
    Status = models.CharField(max_length=100, choices=Status_Choice, default="n/a")
    Month_Date=models.DateField(null=True, blank=True)
    Day_Date=models.DateField(null=True, blank=True)
    Year_Date=models.DateField(null=True, blank=True)
    RefDate=models.DateField(null=True, blank=True)
    Start_AccNo=models.IntegerField(null=True, blank=True)
    End_AccNo=models.IntegerField(null=True, blank=True)
    No_Isolates=models.IntegerField(null=True, blank=True)
    Concordance_Check=models.CharField(max_length=255, blank=True,)
    Concordance_by=models.CharField(max_length=255, blank=True,)
    Concordance_by_Initials=models.CharField(max_length=255, blank=True,)
    abx_code=models.CharField(max_length=25, blank=True, default="")

    
    arsp_Encoder = models.CharField(max_length=255, blank=True, null=True, default="")
    arsp_Enc_Lic = models.CharField(max_length=100,blank=True, null=True, default="")
    arsp_Checker = models.CharField(max_length=255, blank=True, null=True, default="") 
    arsp_Chec_Lic = models.CharField(max_length=100,blank=True, null=True, default="")
    arsp_Verifier = models.CharField(max_length=255, blank=True, null=True, default="")
    arsp_Ver_Lic = models.CharField(max_length=100,blank=True, null=True, default="")
    arsp_LabManager = models.CharField(max_length=255, blank=True, null=True, default="")
    arsp_Lab_Lic = models.CharField(max_length=100,blank=True, null=True, default="")
    arsp_Head = models.CharField(max_length=255, blank=True, null=True, default="")
    arsp_Head_Lic = models.CharField(max_length=100,blank=True, null=True, default="")
    Date_Accomplished_ARSP=models.DateField(blank=True, null=True)
    
    x_mrse = models.CharField(max_length=255, blank=True)
    x_mrsamrse = models.CharField(max_length=255, blank=True)
    x_entbac = models.CharField(max_length=255, blank=True)
    edta = models.CharField(max_length=255, blank=True)




    def save(self, *args, **kwargs):
        # Fill defaults to prevent NULL insertion
        derived_seq = accession_ref_sequence(self.AccessionNo)
        if derived_seq is not None:
            self.bat_seq = derived_seq
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "bat_seq" not in update_fields:
                kwargs["update_fields"] = list(update_fields) + ["bat_seq"]

        self.arsp_Encoder = self.arsp_Encoder or ""
        self.arsp_Enc_Lic = self.arsp_Enc_Lic or ""
        self.arsp_Checker = self.arsp_Checker or ""
        self.arsp_Chec_Lic = self.arsp_Chec_Lic or ""
        self.arsp_Verifier = self.arsp_Verifier or ""
        self.arsp_Ver_Lic = self.arsp_Ver_Lic or ""
        self.arsp_LabManager = self.arsp_LabManager or ""
        self.arsp_Lab_Lic = self.arsp_Lab_Lic or ""
        self.arsp_Head = self.arsp_Head or ""
        self.arsp_Head_Lic = self.arsp_Head_Lic or ""
        self.Site_Pre = self.Site_Pre or ""
        self.Site_Pre_ed = self.Site_Pre_ed or ""
        self.Site_Pos = self.Site_Pos or ""
        self.Site_Pos_ed = self.Site_Pos_ed or ""
        self.Site_Org = self.Site_Org or ""
        self.ars_OrgCode = self.ars_OrgCode or ""
        self.Site_OrgName = self.Site_OrgName or ""
        self.ars_Pre = self.ars_Pre or ""
        self.ars_Pre_ed = self.ars_Pre_ed or ""
        self.ars_Post = self.ars_Post or ""
        self.ars_Post_ed = self.ars_Post_ed or ""



        # Normalize Age for IntegerField
        if self.Age in ("", " ", None):
            self.Age = None

        self.Age_Display = format_age_display(
            self.Date_Birth,
            self.Spec_Date,
            self.Age,
        )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "Age_Display" not in update_fields:
            kwargs["update_fields"] = list(update_fields) + ["Age_Display"]

        super().save(*args, **kwargs)


    def __str__(self):
        return self.AccessionNo
    
    class Meta:
        db_table ="Referred_Data"
        constraints = [
            models.UniqueConstraint(fields=['AccessionNo', 'Batch_Code'], name='unique_accession_batch'),
        
        ]





class ReferredData_upload(models.Model):
    ReferredDataFile = models.FileField(upload_to='uploads/referred/', null=True, blank=True)

    class Meta:
        db_table ="Referred_upload"
  



class SiteData(models.Model):
    SiteCode=models.CharField(max_length=3, blank=True)
    SiteName=models.CharField(max_length=155, blank=True)
    Site_Address = models.CharField(max_length=255, blank=True)
    Site_Lab_Head = models.CharField(max_length=255, blank=True)
    Site_Lab_Head_Credentials = models.CharField(max_length=100, blank=True)
    Site_Lab_Head_Designation = models.CharField(max_length=255, blank=True)
    Site_Lab_Head_Email = models.EmailField(blank=True, validators=[EmailValidator()])
    Site_Lab_Head_Contact = models.CharField(max_length=100, blank=True)
    Site_Med_Ctr_Chief = models.CharField(max_length=255, blank=True)
    Site_Med_Ctr_Chief_Credentials = models.CharField(max_length=100, blank=True)
    Site_Med_Ctr_Chief_Designation= models.CharField(max_length=255, blank=True)
    Site_Med_Ctr_Chief_Email = models.EmailField(blank=True, validators=[EmailValidator()])
    Site_Med_Ctr_Chief_Contact = models.CharField(max_length=100, blank=True)
    Site_MedTech = models.CharField(max_length=255, blank=True)
    Site_MedTech_Credentials = models.CharField(max_length=100, blank=True)
    Site_MedTech_Email = models.EmailField(blank=True, validators=[EmailValidator()])
    Site_MedTech_Contact = models.CharField(max_length=100, blank=True)
    Site_MedTech_Designation = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.SiteCode 
    
class Meta:
    db_table ="SiteData"

class SiteCode_upload(models.Model):
    File_uploadSite = models.FileField(upload_to='uploads/site/', null=True, blank=True)

    class Meta:
        db_table = "SiteCode_upload"

class BreakpointsTable(models.Model):
    TestMethodChoices =(
        ('DISK', 'DISK'),
        ('MIC','MIC'),
    )
    
    GuidelineChoices = (
        ('CLSI', 'CLSI'),        
    )
    
    Emerg_Int_Choices = (
        ('','-'),
        ('R','R'),
        ('I', 'I'),
        ('S', 'S'),
        ('NS','NS'),
        ('SDD','SDD'),

    )
    Antibiotic_list = models.ForeignKey(
        'Antibiotic_List',
        on_delete=models.CASCADE,
        related_name='breakpoints',
        to_field='Whonet_Abx',   # this tells Django to link by the Whonet_Abx field
        db_column='Abx_List_Whonet_Abx',  # keeps database column name clear
        null=True,
        blank=True
    )
    Guidelines = models.CharField(max_length=100, choices=GuidelineChoices, blank=True, default='')
    Year = models.CharField(max_length=100, blank=True, default='')
    Org = models.CharField(max_length=100, blank=True, default='')
    Org_Code_type = models.CharField(max_length=100, blank=True, default='')
    Spec_code = models.CharField(max_length=100, blank=True, null=True)
    Emerging_specimen =models.BooleanField(default=False)
    Test_Method = models.CharField(max_length=20, choices=TestMethodChoices, blank=True, default='')
    Potency = models.CharField(max_length=20, blank=True, default='')
    Abx_code = models.CharField(max_length=15, blank=True, default='')
    Tier = models.CharField(max_length=10, blank=True, default='')
    # Show = models.BooleanField(default=True)
    # Retest = models.BooleanField(default=False)

    Emerging_Org_Flag= models.BooleanField(default=False)
    Emerging_Abx_Flag=models.BooleanField(default=False)
    Emerging_Pheno_Flag=models.CharField(max_length=100, blank=True, default="", choices=Emerg_Int_Choices)
    Emerging_Pheno_Flag_Other=models.CharField(max_length=100, blank=True, default="", choices=Emerg_Int_Choices)
    

    Antibiotic = models.CharField(max_length=100, blank=True, default='')
    Whonet_Abx = models.CharField(max_length=100, blank=True, default='')
    Disk_Abx = models.BooleanField(default=False)
    R_val = models.CharField(max_length=30, blank=True, default='')
    I_val = models.CharField(max_length=30, blank=True, default='')
    SDD_val = models.CharField(max_length=30, blank=True, default='')
    S_val = models.CharField(max_length=30, blank=True, default='')
    Alert_val = models.CharField(max_length=30, blank=True, default='')
    Date_Modified = models.DateField(auto_now_add=True)
    def __str__(self):
        return self.Abx_code 

    #  def save(self, *args, **kwargs):
    #         if self.f_Spec_Type and self.f_Spec_Type.Emerging_Spec_Flag:
    #         self.f_Spec_Emerging = True
    #     else:
    #         self.f_Spec_Emerging = False

    class Meta:
        db_table ="BreakpointsTable"
        ordering = ["Whonet_Abx"]


class Breakpoint_upload(models.Model):
    File_uploadBP = models.FileField(upload_to='uploads/breakpoints/', null=True, blank=True)

    class Meta:
        db_table = "Breakpoint_upload"

    
#for antibiotic test entries
class AntibioticEntry(models.Model):

#  links to main and breakpoints table
    ab_idNum_referred = models.ForeignKey(Referred_Data, on_delete=models.CASCADE, null=True, related_name='antibiotic_entries', to_field='AccessionNo')
    ab_Site_Org = models.CharField(max_length=100, blank=True, null=True)
    ab_Ret_Org = models.CharField(max_length=100, blank=True, null=True)
    
   

    ab_Disk_Abx = models.BooleanField(default=False)
    ab_Ret_Disk_Abx= models.BooleanField(default=False)
    ab_AccessionNo= models.CharField(max_length=100, blank=True, null=True)
    ab_RefNo = models.CharField(max_length=100, blank=True, null=True)
    ab_breakpoints_id = models.ManyToManyField(BreakpointsTable, max_length=6)
    
    ab_Antibiotic = models.CharField(max_length=100, blank=True, null=True)
    ab_Abx_code= models.CharField(max_length=100, blank=True, null=True)
    ab_Abx=models.CharField(max_length=100, blank=True, null=True)

    #sentinel site results
    ab_Disk_value = models.IntegerField(blank=True, null=True)
    ab_Disk_RIS = models.CharField(max_length=4, blank=True) 
    ab_Disk_enRIS = models.CharField(max_length=4, blank=True, default='') 


    ab_MIC_operand=models.CharField(max_length=4, blank=True, null=True, default='')
    ab_MIC_value = models.DecimalField(max_digits=10, decimal_places=5, blank=True, null=True)

    ab_MIC_RIS = models.CharField(max_length=4, blank=True)
    ab_MIC_enRIS = models.CharField(max_length=4, blank=True, default='')
    
    ab_AlertMIC = models.BooleanField(default=False)
    ab_Alert_val = models.CharField(max_length=30, blank=True, null=True, default='')

    ab_R_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    ab_I_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    ab_SDD_breakpoint = models.CharField(max_length=10, blank=True, null=True)  
    ab_S_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    
    #arsrl results
     #### this will apply on retest only
    ab_Org_Flag = models.BooleanField(default=False)
    ab_Abx_Flag = models.BooleanField(default=False)
    ab_Abx_Phenotype = models.CharField(max_length=100, blank=True, null=True)
    ab_Abx_Phenotype_Other = models.CharField(max_length=100, blank=True, null=True)

    ab_Retest_Antibiotic = models.CharField(max_length=100, blank=True, null=True)
    ab_Retest_Abx_code = models.CharField(max_length=100, blank=True, null=True)
    ab_Retest_Abx = models.CharField(max_length=100, blank=True, null=True)
    
    ab_Retest_DiskValue = models.IntegerField(blank=True, null=True, )
    ab_Retest_Disk_RIS = models.CharField(max_length=4, blank=True)
    ab_Retest_Disk_enRIS = models.CharField(max_length=4, blank=True, default='')
    
    ab_Retest_MIC_operand=models.CharField(max_length=4, blank=True, null=True, default='')
    ab_Retest_MICValue = models.DecimalField(max_digits=10, decimal_places=5, blank=True, null=True)

    ab_Retest_MIC_RIS = models.CharField(max_length=4, blank=True)
    ab_Retest_MIC_enRIS = models.CharField(max_length=4, blank=True, default='')    
    
    ab_Retest_AlertMIC = models.BooleanField(default=False)
    ab_Retest_Alert_val = models.CharField(max_length=30, blank=True, null=True, default='')
    ab_Ret_R_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    ab_Ret_I_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    ab_Ret_SDD_breakpoint = models.CharField(max_length=10, blank=True, null=True)
    ab_Ret_S_breakpoint = models.CharField(max_length=10, blank=True, null=True)    

    ab_MICJoined = models.CharField(max_length=7, blank=True, null=True)
    ab_Date_uploaded_rd = models.DateField(auto_now_add=True)
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
        db_table = "AntibioticEntry"


class RawAntibiotic_upload(models.Model):
    RawAntibioticFile = models.FileField(upload_to='uploads/raw/antibiotic-entries/', null=True, blank=True)

    class Meta:
        db_table ="RawAntibiotic_upload"


class SpecimenTypeModel(models.Model):
    Emerging_Spec_Flag= models.BooleanField(default=False)
    Specimen_code = models.CharField(max_length=4, unique=True, db_index=True, blank=True, null=True)
    Specimen_name = models.CharField(max_length=100, blank=True, null=True)
    Specimen_Code_Grp = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True)
    Specimen_Grp_Name = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        # Always return a string; prefer Specimen_code, fallback to placeholder
        return str(self.Specimen_code) or "n/a"
    
    class Meta:
        db_table = "SpecimenTypeTable"


class Specimen_upload(models.Model):
    File_uploadSpec = models.FileField(upload_to='uploads/specimen/', null=True, blank=True)

    class Meta:
        db_table = "Specimen_upload"



#Address Book
class arsStaff_Details(models.Model):
    Role_choices = (
        ('',''),
        ('DMU Encoder', 'DMU Encoder'),
        ('LAB Encoder', 'LAB Encoder'),
        ('Secretariat', 'Secretariat'),
        ('Verifier','Verifier'),
        ('Checker', 'Checker'),
        ('Manager', 'Manager'),
        ('Admin', 'Admin'),

    )

    User_Account = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="arsp_staff_profile",
    )
    Staff_Name = models.CharField(max_length=100, blank=True, null=True)
    Staff_Designation= models.CharField(max_length=100, blank=True, null=True)
    Staff_Telnum= PhoneNumberField(blank=True, region="PH", null=True)
    Staff_EmailAdd = models.EmailField(max_length=100, blank=True, null=True)
    Staff_License = models.CharField(max_length=100, blank=True, null=True)
    Staff_Credentials = models.CharField(max_length=100, blank=True, default="")
    Staff_Role = models.CharField(max_length=150, choices=Role_choices,  blank=True, default="")
    Is_Default_Lab_Manager = models.BooleanField(default=False)
    Is_Default_Head = models.BooleanField(default=False)


    @property
    def role_list(self):
        return [
            role.strip()
            for role in str(self.Staff_Role or "").replace(",", "|").replace(";", "|").split("|")
            if role.strip()
        ]

    @property
    def display_roles(self):
        roles = self.role_list
        return ", ".join(roles) if roles else "Checker"

    @property
    def display_name(self):
        name = (self.Staff_Name or "").strip()
        if not name and self.User_Account_id:
            name = (
                self.User_Account.get_full_name()
                or self.User_Account.username
                or self.User_Account.email
                or ""
            ).strip()
        credentials = (self.Staff_Credentials or "").strip()
        if name and credentials:
            return f"{name}, {credentials}"
        return name or "Unnamed Staff"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.Is_Default_Lab_Manager:
            arsStaff_Details.objects.exclude(pk=self.pk).filter(Is_Default_Lab_Manager=True).update(
                Is_Default_Lab_Manager=False
            )
        if self.Is_Default_Head:
            arsStaff_Details.objects.exclude(pk=self.pk).filter(Is_Default_Head=True).update(
                Is_Default_Head=False
            )

    def __str__(self):
        return self.display_name


class UserProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    Middle_Name = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        db_table = "UserProfile"

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Recommendation(models.Model):
    Reco_Code = models.CharField(max_length=100, blank=True, null=True)
    Reco_Details = models.TextField(blank=True, null=True)

    def __str__(self):
            return self.Reco_Code 
    

class FieldMapping(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    raw_field = models.CharField(max_length=255)
    mapped_field = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_retest = models.BooleanField(default=False)
    

    class Meta:
        unique_together = ('user', 'raw_field')

    def __str__(self):
        return f"{self.user.username}: {self.raw_field} → {self.mapped_field}"


class Antibiotic_List(models.Model):
    TestMethodChoices =(
        ('DISK', 'DISK'),
        ('MIC','MIC'),
    )
    
    GuidelineChoices = (
        ('CLSI', 'CLSI'),        
    )
    Show=models.BooleanField(default=True) #Show in Sentinel Antibiotics in data entry
    Retest=models.BooleanField(default=True) # Show in Retest antibiotics in data entry
    Show_Site=models.BooleanField(default=True) # Show in Sentinel AST Result
    Show_Ars = models.BooleanField(default=True) # Show in ARSRL AST Result
    Show_Value=models.BooleanField(default=True) # Show Encoded Value in Laboratory Result, if false, no value will be displayed in the laboratory result
    Show_Panel =models.BooleanField(default=False) # Show only panel antibiotics in the form filtering
    Show_All =models.BooleanField(default=True) # Show in All Antibiotics in the form filtering
    Disk_Abx=models.BooleanField(default=True) 
    Tier = models.CharField(max_length=10, blank=True, default='')
    Test_Method=models.CharField(max_length=100, choices=TestMethodChoices, blank=True, default="")
    Abx_code=models.CharField(max_length=100, blank=True, default="",)
    Whonet_Abx=models.CharField(max_length=100, blank=True, default="", unique=True)
    Antibiotic=models.CharField(max_length=100, blank=True, default="")
    Guidelines=models.CharField(max_length=100, choices=GuidelineChoices, blank=True, default="")
    Potency=models.CharField(max_length=100, blank=True, default="")
    Class=models.CharField(max_length=100, blank=True, default="")
    Subclass=models.CharField(max_length=100, blank=True, default="")
    Date_Modified=models.DateField(auto_now_add=True, null=True)

    class Meta:
        db_table = "Antibiotic_List"

    def __str__(self):
        return f"{self.Whonet_Abx}"


class Antibiotic_upload(models.Model):
    File_uploadAbx = models.FileField(upload_to='uploads/breakpoints/', null=True, blank=True)

    class Meta:
        db_table = "Antibiotic_upload"


class Organism_List(models.Model):
    Whonet_Org_Code= models.CharField(max_length=20, unique=True)
    Replaced_by = models.CharField(max_length=20, null=True, blank=True)
    Organism = models.CharField(max_length=255)
    Organism_Type = models.CharField(max_length=5, null=True, blank=True)
    Family_Code = models.CharField(max_length=20, null=True, blank=True)
    Genus_Group = models.CharField(max_length=50, null=True, blank=True)
    Genus_Code = models.CharField(max_length=20, null=True, blank=True)
    Species_Group = models.CharField(max_length=50, null=True, blank=True)
    Serovar_Group = models.CharField(max_length=50, null=True, blank=True)
    Kingdom = models.CharField(max_length=100, null=True, blank=True)
    Phylum = models.CharField(max_length=100, null=True, blank=True)
    Class = models.CharField(max_length=100, null=True, blank=True)
    Order = models.CharField(max_length=100, null=True, blank=True)
    Family= models.CharField(max_length=100, null=True, blank=True)
    Genus = models.CharField(max_length=100, null=True, blank=True)

    UPPERCASE_CODE_FIELDS = (
        "Family_Code",
        "Genus_Code",
        "Genus_Group",
        "Species_Group",
        "Serovar_Group",
    )

    def save(self, *args, **kwargs):
        if self.Whonet_Org_Code:
            self.Whonet_Org_Code = self.Whonet_Org_Code.strip().lower()

        for field in self.UPPERCASE_CODE_FIELDS:
            value = getattr(self, field, None)
            if value:
                setattr(self, field, value.strip().upper())

        super().save(*args, **kwargs)


    def __str__(self):
        return f"{self.Whonet_Org_Code}"


class Organism_upload(models.Model):
    File_uploadOrg = models.FileField(upload_to='uploads/organism/', null=True, blank=True)

    class Meta:
        db_table = "Organism_upload"






class Emerging_Filter_Age(models.Model):

    Eme_Age=models.IntegerField(blank=True, null=True)
    class Meta:
        db_table = "Emerging_Filter_Age"
    
    def __str__(self):
        return str(self.Eme_Age)

class Emerging_Crit_upload(models.Model):
    File_uploadEme = models.FileField(upload_to='uploads/emerging/', null=True, blank=True)

    class Meta:
        db_table = "Emerging_upload"



class Phenotype_Pre(models.Model):
    Pre_Phenotypes = models.CharField(max_length=255, blank=True, null=True)


    class Meta:
        db_table = "Phenotype_Pre"

    
    def __str__(self):
        return f"{self.Pre_Phenotypes}"

class Pheno_upload_Pre(models.Model):
    File_Pheno_pre = models.FileField(upload_to='uploads/pheno_pre/', null=True, blank=True)


class Phenotype_Post(models.Model):
    Post_Phenotypes = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "Phenotype_Post"

    def __str__(self):
        return self.Post_Phenotypes or ""


class Pheno_upload_Post(models.Model):
    File_Pheno_post = models.FileField(
        upload_to='uploads/pheno_post/',
        null=True,
        blank=True
    )

    class Meta:
        db_table = "Pheno_upload_Post"


class Recommendation_items(models.Model):
    RecoCode = models.CharField(max_length=100, blank=True, null=True)
    Description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "Recommendation_items"
    
    def __str__(self):
        return f"{self.RecoCode}" or ""

class Reco_item_upload(models.Model):
    File_reco_desc = models.FileField(upload_to='uploads/reco_desc/', null=True, blank=True)




class TATLocation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]
        db_table = "TATLocation"

    def __str__(self):
        return self.name


class TATOverallSetting(models.Model):
    target_days = models.PositiveIntegerField(default=40)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"target_days": 40})
        return obj

    @classmethod
    def get_target_days(cls):
        return cls.get_solo().target_days

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    class Meta:
        db_table = "TATOverallSetting"
        verbose_name = "TAT Overall Setting"
        verbose_name_plural = "TAT Overall Settings"

    def __str__(self):
        return f"Overall target: {self.target_days} days"


class TATform(models.Model):

    tat_Batch_Isolates = models.OneToOneField(
        "Batch_Table",
        on_delete=models.CASCADE,
        related_name="tat_entry"
    )

    tat_SiteCode = models.CharField(max_length=10, blank=True)
    tat_Batch_Code = models.CharField(max_length=255, blank=True)
    tat_Referral_Date = models.DateField(null=True, blank=True)
    tat_BatchNumber = models.CharField(max_length=255, blank=True)
    tat_Total_Batch = models.CharField(max_length=100, blank=True)
    tat_Num_Isolate = models.PositiveIntegerField(null=True, blank=True)
    tat_Scanning_raw = models.BooleanField(default=False)
    tat_Scanning_ws = models.BooleanField(default=False)
    tat_Scanning_final = models.BooleanField(default=False)
    tat_Batch_Location = models.CharField(
        max_length=100,
        default='n/a'
    )

    tat_Target_Days = models.PositiveIntegerField(null=True, blank=True)
    tat_Date_Released = models.DateField(null=True, blank=True)

    tat_Running_TAT = models.PositiveIntegerField(null=True, blank=True)
    tat_Final_TAT = models.PositiveIntegerField(null=True, blank=True)

    tat_Status_Release = models.CharField(
        max_length=20,
        choices=(
            ('Ongoing', 'Ongoing'),
            ('Released', 'Released'),
            ('Overdue', 'Overdue'),
        ),
        default='Ongoing'
    )

    tat_Remarks = models.TextField(blank=True, null=True)
    tat_Date_Last_Update = models.DateField(null=True, blank=True)



    def save(self, *args, **kwargs):

        today = timezone.now().date()
        self.tat_Target_Days = TATOverallSetting.get_target_days()
        receipt_date = self.tat_Referral_Date
        if self.pk:
            first_step_received = (
                self.steps
                .filter(date_received__isnull=False)
                .order_by("date_received")
                .values_list("date_received", flat=True)
                .first()
            )
            receipt_date = first_step_received or receipt_date

        if receipt_date:

            # 🔹 If Released
            if self.tat_Date_Released:

                self.tat_Final_TAT = working_days(
                    receipt_date,
                    self.tat_Date_Released
                )

                self.tat_Running_TAT = self.tat_Final_TAT
                self.tat_Status_Release = "Released"

            else:
                # 🔹 Ongoing
                self.tat_Running_TAT = working_days(
                    receipt_date,
                    today
                )

                if self.tat_Target_Days and self.tat_Running_TAT:
                    if self.tat_Running_TAT > self.tat_Target_Days:
                        self.tat_Status_Release = "Overdue"
                    else:
                        self.tat_Status_Release = "Ongoing"

                self.tat_Final_TAT = None

        self.tat_Date_Last_Update = today

        super().save(*args, **kwargs)

    @property
    def current_step(self):
        return self.steps.order_by('-id').first()

    @property
    def lab_steps(self):
        return self.steps.filter(step_owner='LAB')

    @property
    def dmu_steps(self):
        return self.steps.filter(step_owner='DMU')

    @property
    def lab_within_tat(self):
        return self.lab_steps.filter(within_tat=True).count()

    @property
    def lab_outside_tat(self):
        return self.lab_steps.filter(within_tat=False).count()

    @property
    def dmu_within_tat(self):
        return self.dmu_steps.filter(within_tat=True).count()

    @property
    def dmu_outside_tat(self):
        return self.dmu_steps.filter(within_tat=False).count()

    @property
    def total_steps(self):
        return self.steps.count()

    @property
    def total_within_tat(self):
        return self.steps.filter(within_tat=True).count()

    @property
    def compliance_percentage(self):
        total = self.steps.exclude(within_tat=None).count()
        if total == 0:
            return 0
        return round((self.total_within_tat / total) * 100, 2)



    class Meta:
        db_table = "TATform"




class TATStep(models.Model):

    tat = models.ForeignKey(
        TATform,
        on_delete=models.CASCADE,
        related_name="steps"
    )

    step_config = models.ForeignKey(
        "TATStepConfig",
        on_delete=models.CASCADE,
        related_name="tat_steps"
    )



    step_type = models.CharField(max_length=500)
    step_owner = models.CharField(max_length=10, blank=True)

    step_days_count = models.PositiveIntegerField(null=True, blank=True)
    within_tat = models.BooleanField(null=True, blank=True)

    date_received = models.DateField(null=True, blank=True)
    date_finished = models.DateField(null=True, blank=True)

    performed_by = models.ForeignKey(
        "arsStaff_Details",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    remarks = models.TextField(blank=True, null=True)

    @property
    def running_days(self):
        if self.date_received and not self.date_finished:
            return (timezone.now().date() - self.date_received).days
        return None

    def save(self, *args, **kwargs):

        # Always extract from config
        if self.step_config:
            self.step_type = self.step_config.step_type
            self.step_owner = self.step_config.step_owner
            target_days = self.step_config.target_days
        else:
            target_days = None

        if self.date_received and self.date_finished:

            self.step_days_count = working_days(
                self.date_received,
                self.date_finished,
                self.step_owner
            )

            if target_days is not None and self.step_days_count is not None:
                self.within_tat = self.step_days_count <= target_days
            else:
                self.within_tat = None

        else:
            self.step_days_count = None
            self.within_tat = None

        super().save(*args, **kwargs)

    def __str__(self):
        if self.step_config:
            return f"{self.step_config.step_type} - {self.tat.tat_Batch_Code}"
        return f"Unconfigured Step - {self.tat.tat_Batch_Code}"




class TATStepConfig(models.Model):

    STEP_OWNER = (
        ('n/a', 'n/a'),
        ('LAB', 'LAB'),
        ('DMU', 'DMU'),
    )

    step_type = models.CharField(max_length=500)
    step_owner = models.CharField(max_length=255, choices=STEP_OWNER, default='n/a')
    target_days = models.PositiveIntegerField()
    order = models.PositiveIntegerField()

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.step_type} ({self.step_owner})"



class TATStepConfigUpload(models.Model):
    tat_file = models.FileField(upload_to='uploads/TAT/', null=True, blank=True)

    class Meta:
        db_table = "TATStepConfigUpload"


class NonWorkingDay(models.Model):

    APPLIES_TO_CHOICES = (
        ('ALL', 'ALL'),
        ('LAB', 'LAB'),
        ('DMU', 'DMU'),
    )

    date = models.DateField(unique=True)
    description = models.CharField(max_length=255)
    applies_to = models.CharField(
        max_length=10,
        choices=APPLIES_TO_CHOICES,
        default='ALL'
    )
    is_recurring = models.BooleanField(
        default=False,
        help_text="Apply this non-working day every year using the same month and day.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.date} - {self.description}"
